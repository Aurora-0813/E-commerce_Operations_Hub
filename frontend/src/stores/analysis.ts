// ══════════════════════════════════════════════════════════════
//  分析（全链）的全局状态（stores/analysis.ts）—— 2026-11-03 新增
// ══════════════════════════════════════════════════════════════
// **解决的问题**（用户反馈）
//   "触发全链不应该在数据接口那吗？它不是危险操作" ✅
//      → 「开始分析」挪到「数据接入」页，主按钮位置 ✅
//   "链的运行状态不透明，要流程图示（Element Steps）" ✅
//      → 本 store 提供 9 个阶段的状态 + 一句话说明 ✅
//   "并且可以后台运行，不影响其他页面点击" ✅
//      → **轮询在 store 里**（不在页面里），切页面/关页面都不中断 ✅
//      → `run_id` 存 localStorage → 刷新能恢复 ✅
//   顶部导航栏一条细进度条 → 任何页面都看得到 ✅
// ══════════════════════════════════════════════════════════════
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import http from '@/api/client'
// **真的在后端停/删**（2026-11-04 ✅）
import { cancelRun, deleteRun } from '@/api/write'

/** **8 个阶段**（和后端 `hub/queue/stages.py` 一一对应）
 *
 * ⚠️ 2026-11-04：「看图片」已从链里移除
 *   图片走独立批次：数据接入 → 图片包（zip）→ 对账 → 自动入队
 *   原因：图片和链没有数据依赖，留在链里会让人误以为要等图片
 */
export const STAGES: Array<{ key: string; title: string; what: string }> = [
  { key: 'ingest', title: '导入', what: '把你的导出文件写进库（表头映射 + 脱敏 + 去重）' },
  { key: 'dqe', title: '降噪', what: '筛掉灌水/广告/无意义，大约砍掉 40%' },
  { key: 'topic', title: '分类', what: '把差评分到 9 个问题类型（支持多标签）' },
  { key: 'align', title: '跨平台对齐', what: '同一个商品在淘宝和京东对上号' },
  { key: 'cluster', title: '聚类', what: '把同一类问题聚成一个「问题簇」' },
  { key: 'price', title: '比价', what: '找跨平台价格异常' },
  { key: 'priority', title: '排优先级', what: '按差评量 × 增长 × GMV × 可解性 算 P0~P3' },
  { key: 'propose', title: '生成建议', what: '每个簇生成一条带依据的处置提案' },
]

const LS_KEY = 'hub_analysis_run'

interface RunResp {
  run_id?: string
  status?: string
  current_stage?: string | null
  started_at?: string | null
  finished_at?: string | null
  degraded?: boolean
  error?: string | null
  /** **阶段内进度**（2026-11-04 加）：{stage, done, total, pct} ✅ */
  progress?: { stage?: string; done?: number; total?: number; pct?: number } | null
  params?: Record<string, unknown>
}

export const useAnalysisStore = defineStore('analysis', () => {
  /** 当前这次分析的 run_id（null = 没有在跑的历史 ✅）*/
  const runId = ref<string>(localStorage.getItem(LS_KEY) ?? '')
  const status = ref<string>('')          // QUEUED / RUNNING / DONE / DEGRADED / FAILED / DEAD
  const currentStage = ref<string>('')
  const startedAt = ref<string>('')
  const finishedAt = ref<string>('')
  const degraded = ref(false)
  const errorMsg = ref('')
  const pollError = ref('')
  /**
   * **当前正在跑的阶段**（给界面用 ✅）
   *
   * ⚠️ 2026-11-04 加：两个字段语义不同
   *    · `currentStage`  = **最后跑完**的阶段（后端用它做**断点续跑** ✅ 所以滞后一拍）
   *    · `progress.stage` = **正在跑**的阶段（progress 上报的 ✅ 实时）
   *    → 界面显示必须用这个，否则"topic 在跑、界面显示 dqe" ✅ ✗
   */
  const activeStage = computed<string>(() =>
    (progress.value?.stage as string) || currentStage.value)

  /** 阶段内进度（后端上报的 ✅）*/
  const progress = ref<{ stage?: string; done?: number; total?: number; pct?: number } | null>(null)
  /** 完成通知只弹一次 ✅ */
  const notified = ref(false)

  let timer: ReturnType<typeof setInterval> | null = null

  /** 正在跑（QUEUED 或 RUNNING）*/
  const running = computed(() => status.value === 'QUEUED' || status.value === 'RUNNING')
  /** 跑完了（成功或降级）*/
  const finished = computed(() =>
    status.value === 'DONE' || status.value === 'DEGRADED')
  /** 挂了 */
  const failed = computed(() =>
    status.value === 'FAILED' || status.value === 'DEAD')

  /** 9 个阶段各自的完成状态（给 ElSteps 的 active ✅）*/
  const stepIndex = computed(() => {
    if (finished.value) return STAGES.length
    const i = STAGES.findIndex((s) => s.key === activeStage.value)
    return i < 0 ? 0 : i
  })

  /** 顶部细进度条的百分比（9 个阶段均分 ✅）*/
  /**
   * 总百分比 = (已完成阶段数 + 当前阶段内百分比) / 总阶段数 ✅
   *
   * ⚠️ 2026-11-04 改：原来**只在阶段边界跳**（16→27→38…）
   *    而 `topic`（分类）要跑 3~4 分钟 → 这 4 分钟百分比一动不动 ✅ ✗
   *    → 现在读后端上报的 `progress.pct`（分类每 10 条一批 ✅）
   */
  const percent = computed<number>(() => {
    if (finished.value) return 100
    if (!runId.value) return 0
    const n = STAGES.length
    const i = Math.max(0, stepIndex.value)
    const inner = (progress.value?.pct ?? 0) / 100
    return Math.min(99, Math.round(((i + inner) / n) * 100))
  })

  /** 每个阶段的状态：wait / process / finish / error */
  function stageStatus(key: string): 'wait' | 'process' | 'success' | 'error' {
    if (failed.value) {
      return stageIndex(key) === stepIndex.value ? 'error' : (
        stageIndex(key) < stepIndex.value ? 'success' : 'wait')
    }
    const i = stageIndex(key)
    if (finished.value) return 'success'
    if (i < stepIndex.value) return 'success'
    if (i === stepIndex.value) return running.value ? 'process' : 'wait'
    return 'wait'
  }

  function stageIndex(key: string): number {
    return STAGES.findIndex((s) => s.key === key)
  }

  /** 拉一次状态（不重试、不抛 ✅）*/
  async function fetchOnce(): Promise<void> {
    if (!runId.value) return
    try {
      const { data } = await http.get<RunResp>(`/api/runs/${runId.value}`)
      status.value = String(data.status ?? '')
      currentStage.value = String(data.current_stage ?? '')
      // **阶段内进度**（2026-11-04 ✅）—— 之前漏了这行，百分比一直是 0 ✅ ✗
      progress.value = data.progress ?? null
      startedAt.value = String(data.started_at ?? '')
      finishedAt.value = String(data.finished_at ?? '')
      degraded.value = Boolean(data.degraded)
      errorMsg.value = String(data.error ?? '')
      pollError.value = ''
      if (!running.value) stopPolling()
    } catch (e) {
      // ⚠️ **404 说明这条 run 已经不在了**（库被清过 / 历史被删 ✅）
      //    用户反馈："进去有分析失败和任务不存在提示" ✅ ✗
      //    → 这种情况**不是失败**，是"没有正在跑的任务" ✅
      //    → **静默清掉**，界面上什么都不显示 ✅
      const st = (e as { status?: number })?.status
      if (st === 404) {
        reset()
        return
      }
      pollError.value = String(e)
      stopPolling()
      status.value = 'FAILED'
    }
  }

  function stopPolling(): void {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 每 3 秒轮询一次（**在 store 里，切页面不中断** ✅）*/
  function startPolling(): void {
    stopPolling()
    void fetchOnce()
    // ⚠️ **`fetchOnce` 后面必须有 `()`**（2026-11-04 修的真 bug）
    //    原来写的是 `void fetchOnce`（没有括号）
    //    → 每 3 秒**只是取了一下函数引用，根本没调用** ✅ ✗
    //    → 表现：界面一直停在 0%，必须手动刷新才更新 ✅
    //    另外按用户要求：**间隔 3 秒 → 2 秒**（点了之后更快看到进度 ✅）
    timer = setInterval(() => { void fetchOnce() }, 2000)
  }

  /** 开始一次分析（提交全链）*/
  async function start(): Promise<{ ok: boolean; existing?: boolean; message?: string }> {
    try {
      const { data } = await http.post<RunResp>('/api/runs', { kind: 'full' })
      runId.value = String(data.run_id ?? '')
      localStorage.setItem(LS_KEY, runId.value)
      status.value = 'QUEUED'
      currentStage.value = ''
      errorMsg.value = ''
      startPolling()
      return { ok: true }
    } catch (e: unknown) {
      // ⚠️ **409 = 已经有一条在跑**（幂等，不是错误 ✅）→ 接上那条
      //    2026-11-04 修：`ApiError` 的字段是 **`runId`**（不是 `body.run_id` ✅）
      //    → 我之前读错 → 409 被当成失败 → 界面弹「ApiError: 已经有一条全链…」✅ ✗
      const err = e as { status?: number; runId?: string | null; code?: string }
      if (err?.status === 409) {
        if (err.runId) {
          runId.value = err.runId
          localStorage.setItem(LS_KEY, runId.value)
          startPolling()
          return { ok: true, existing: true, message: '已经有一条分析在跑，帮你接上了' }
        }
        // 409 但没带 id（极少见）→ 也不弹红错，让用户刷新看进度 ✅
        return { ok: true, existing: true,
                 message: '已经有一条分析在跑 —— 刷新页面就能看到它的进度' }
      }
      return { ok: false, message: String(e) }
    }
  }

  /** **真的在后端停止**（2026-11-04 加 ✅）*/
  async function cancel(): Promise<{ ok: boolean; message?: string }> {
    if (!runId.value) return { ok: false, message: '没有正在跑的任务' }
    const r = await cancelRun(runId.value)
    // 后端已置 CANCELLED → 本地立刻反映（不用等下一轮轮询 ✅）
    status.value = 'CANCELLED'
    stopPolling()
    return r
  }

  /** **真的在后端删除**（连同簇和提案 ✅）*/
  async function remove(): Promise<{ ok: boolean; message?: string }> {
    if (!runId.value) return { ok: false, message: '没有可删的任务' }
    const r = await deleteRun(runId.value)
    reset()
    return r
  }

  /**
   * **切去看另一条 run**（2026-11-04 加）
   *
   * 为什么需要：前端只认 localStorage 里的 run_id ✅
   *   → 如果那条被后端标了 DEAD（比如运维清场 ✅），
   *     而库里**另有**一条正在跑 ✅
   *   → 就会出现"提示条说正在跑 75%、步骤条说执行失败"的自相矛盾 ✅ ✗
   *   → 这时调这个，前端自动接到真正在跑的那条 ✅
   */
  function adopt(id: string): void {
    if (!id || id === runId.value) return
    runId.value = id
    localStorage.setItem(LS_KEY, id)
    status.value = 'QUEUED'
    currentStage.value = ''
    progress.value = null
    errorMsg.value = ''
    startPolling()
  }

  /** 清掉本地记录（**不取消后端任务** ✅）*/
  function reset(): void {
    stopPolling()
    runId.value = ''
    status.value = ''
    currentStage.value = ''
    localStorage.removeItem(LS_KEY)
  }

  /** 页面加载时自动恢复（刷新/切页回来 ✅）*/
  function restore(): void {
    const saved = localStorage.getItem(LS_KEY)
    if (saved) {
      runId.value = saved
      startPolling()
    }
  }

  return {
    notified,
    runId, status, currentStage, startedAt, finishedAt,
    degraded, errorMsg, pollError,
    running, finished, failed, stepIndex, percent,
    stageStatus, stageIndex,
    start, reset,
    cancel,
    remove, restore, stopPolling, fetchOnce,
    adopt,
  }
})
