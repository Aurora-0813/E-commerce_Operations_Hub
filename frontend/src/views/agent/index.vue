<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  5 号页：Agent 任务（形态③：步骤代码定、参数代码注入、模型只判一次）
//  流程（**这就是异步任务的标准用法** ✅）：
//    ① 选簇 → POST /api/handle → **202 + run_id**（同簇重复 → 409 + 已有 run_id ✅）
//    ② 轮询 GET /api/runs/{run_id} 直到 DONE/DEGRADED/FAILED/DEAD
//    ③ DONE 后 `params.agent_run_id` 才是**结果** id
//    ④ GET /api/handle/{agent_run_id}        → 4 步轨迹 + 结论
//    ⑤ GET /api/handle/{agent_run_id}/trace  → 每步 token / 耗时
//    ⑥ POST /api/handle/{agent_run_id}/adopt → 生成提案
// ⚠️ 轮询必须清定时器（组件卸载 / 任务结束都要清 ✅）
// ══════════════════════════════════════════════════════════════
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElAlert, ElButton, ElCard, ElMessage, ElOption, ElSelect, ElTag,
} from 'element-plus'
import { MagicStick, Refresh } from '@element-plus/icons-vue'

import http from '@/api/client'
import { getClusters, type ClusterRow } from '@/api/data'
import { adoptRun, getRun, postHandle, type RunStatus } from '@/api/write'
import { ApiError } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import PageLoading from '@/components/PageLoading.vue'

const router = useRouter()

// ---------- 选簇 ----------
const clusters = ref<ClusterRow[]>([])
const clusterId = ref<string>('')

async function loadClusters(): Promise<void> {
  try {
    clusters.value = await getClusters('reviews')
    if (!clusterId.value && clusters.value.length) clusterId.value = clusters.value[0].id
  } catch {
    clusters.value = []
  }
}
onMounted(() => void loadClusters())

// ---------- 提交 + 轮询 ----------
const submitting = ref(false)
const run = ref<RunStatus | null>(null)
const errorMsg = ref('')

/** 轮询定时器（**必须清** ✅）*/
let pollTimer: number | null = null
let pollCount = 0
const MAX_POLL = 60 // 2 分钟

function stopPoll(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}
onBeforeUnmount(stopPoll)

const isRunning = computed(() => {
  const s = run.value?.status
  return s === 'QUEUED' || s === 'RUNNING'
})

async function submit(): Promise<void> {
  if (!clusterId.value) return
  submitting.value = true
  errorMsg.value = ''
  stopPoll()
  try {
    const r = await postHandle(clusterId.value)
    await startPolling(r.run_id)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.runId) {
      // **409 是幂等**：这个簇已经有任务了 → 直接去轮询它 ✅
      ElMessage.info('这个簇已经有任务在跑，直接看它的进度 ✅')
      await startPolling(e.runId)
    } else if (e instanceof ApiError) {
      errorMsg.value = e.message
    }
  } finally {
    submitting.value = false
  }
}

async function startPolling(runId: string): Promise<void> {
  pollCount = 0
  const tick = async (): Promise<void> => {
    pollCount += 1
    try {
      run.value = await getRun(runId)
    } catch {
      stopPoll()
      return
    }
    const s = run.value.status
    if (s === 'DONE' || s === 'DEGRADED' || s === 'FAILED' || s === 'DEAD') {
      stopPoll()
      if (run.value.agent_run_id) await loadAgentRun(run.value.agent_run_id)
      return
    }
    if (pollCount >= MAX_POLL) {
      stopPoll()
      ElMessage.warning('任务还在跑（超过 2 分钟），去 /ops 看 ✅')
    }
  }
  await tick()
  if (!isRunning.value) return
  pollTimer = window.setInterval(() => void tick(), 2000)
}

// ---------- agent_run 轨迹 ----------
interface AgentStep {
  idx: number
  tool?: string
  args?: Record<string, unknown>
  thought?: string
  error?: string
}
interface AgentRunResp {
  id: string
  status: string
  cluster_id: string
  goal?: string
  topic?: string
  steps?: AgentStep[]
  evidence?: Array<{ id?: string; source?: string; ref?: string; text?: string }>
  result?: {
    tool?: string
    recommendation?: string
    confidence?: number
    why_not_rule?: string
  }
  critique?: Record<string, unknown>
  proposal_id?: string | null
  /**
   * ⚠️ **token 直接用 agent_run 自带的这两个字段**（2026-11-01 修）
   *    不要靠 `/trace` 去"求和 span" ✅
   *    实测：`agent_run.tokens_in/out` = 667/61 ✅ 一直是对的；
   *          而 `/api/handle/{id}/trace` 因为 span 的 trace_id 和 run 的不一致，
   *          经常返回 **0 条** ✅ ✗
   */
  tokens_in?: number
  tokens_out?: number
}
interface TraceSpan { name: string; in_tokens?: number; out_tokens?: number; ms?: number }

const agentRun = ref<AgentRunResp | null>(null)
const trace = ref<TraceSpan[]>([])
const runLoading = ref(false)

async function loadAgentRun(agentRunId: string): Promise<void> {
  runLoading.value = true
  try {
    const [{ data: r }, tRes] = await Promise.all([
      http.get<AgentRunResp>(`/api/handle/${agentRunId}`),
      // ⚠️ **返回的是 `{run_id, trace_id, items}`，不是数组**（2026-11-01 修）
      //    我原来写成 `http.get<TraceSpan[]>` → `trace.value = t`（一个 dict ✅）
      //    → 模板里 `v-for="s in trace"` **抛 TypeError: trace.value is not iterable** ✅ ✗
      //    → 整个 agent 页的「轨迹」区块渲染失败 ✅
      http
        .get<{ items?: TraceSpan[] }>(`/api/handle/${agentRunId}/trace`)
        .catch(() => ({ data: { items: [] as TraceSpan[] } })),
    ])
    agentRun.value = r
    trace.value = tRes.data?.items ?? []
  } catch (e) {
    if (e instanceof ApiError) errorMsg.value = e.message
  } finally {
    runLoading.value = false
  }
}

// ---------- 采纳 ----------
const adopting = ref(false)
async function adopt(): Promise<void> {
  const id = agentRun.value?.id
  if (!id) return
  adopting.value = true
  try {
    const r = await adoptRun(id)
    ElMessage.success(r.already_adopted ? '这条已经采纳过了（幂等 ✅）' : '已生成提案')
    if (r.proposal_id) void router.push(`/proposals/${r.proposal_id}`)
  } finally {
    adopting.value = false
  }
}

// ---------- 展示 ----------
const TOOL_LABEL: Record<string, string> = {
  query_cluster: '查簇信息',
  query_reviews: '查评价',
  query_history: '查历史战绩',
  query_product: '查商品',
  'review.draft_reply': '生成回复草稿',
  'listing.update_desc': '修改商品描述',
  'product.update_price': '修改价格',
}
const STATUS_LABEL: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '运行中', DONE: '完成',
  DEGRADED: '完成（降级）', FAILED: '失败', DEAD: '进死信',
}
/** 每步的 token 汇总（`trace_span` 里按 name 找 llm.* ✅）*/
const tokenSummary = computed(() => {
  // ⚠️ **优先用 agent_run 自带的 tokens_in/out**（**权威、一直有** ✅）
  const r = agentRun.value
  if (r && (r.tokens_in || r.tokens_out)) {
    return { inTok: r.tokens_in ?? 0, outTok: r.tokens_out ?? 0 }
  }
  // 退化：从 trace 求和（只有 span 的 trace_id 对得上时才有 ✅）
  let inTok = 0
  let outTok = 0
  for (const sp of trace.value) {
    if (sp.name?.startsWith('llm')) {
      inTok += sp.in_tokens ?? 0
      outTok += sp.out_tokens ?? 0
    }
  }
  return { inTok, outTok }
})
</script>

<template>
  <div class="agent">
    <div class="agent__head">
      <div>
        <h2 class="agent__title">Agent 任务</h2>
        <div class="agent__sub">
          形态③：<b>步骤由代码定、参数由代码注入、模型只做一次判断</b> ✅
          （7B 实测收尾率 0%，所以不让它自己规划）
        </div>
      </div>
    </div>

    <!-- 选簇 + 提交 -->
    <ElCard shadow="never">
      <div class="agent__form">
        <ElSelect v-model="clusterId" class="agent__select" placeholder="选一个簇" filterable>
          <ElOption v-for="c in clusters" :key="c.id" :value="c.id"
                    :label="`${c.label}（${c.tier} · ${c.reviews} 条）`" />
        </ElSelect>
        <ElButton type="primary" :icon="MagicStick" :loading="submitting || isRunning"
                  :disabled="!clusterId" @click="submit()">
          让 agent 出方案
        </ElButton>
        <span class="agent__hint">
          提交后是 <b>202</b>，会轮询到 DONE 才拿结果 ✅
        </span>
      </div>

      <ElAlert v-if="errorMsg" :title="errorMsg" type="error" show-icon :closable="false"
               class="agent__alert" />

      <!-- 任务状态 -->
      <div v-if="run" class="agent__run">
        <ElTag :type="isRunning ? 'warning' : (run.status === 'DONE' ? 'success' : 'info')"
               size="small" effect="dark">
          {{ STATUS_LABEL[run.status] ?? run.status }}
        </ElTag>
        <span class="agent__run-meta">run {{ run.run_id.slice(0, 8) }}</span>
        <span v-if="run.current_stage" class="agent__run-meta">阶段：{{ run.current_stage }}</span>
        <span v-if="run.error" class="agent__run-err">{{ run.error }}</span>
        <ElButton v-if="isRunning" size="small" text :icon="Refresh" disabled>
          轮询中…（每 2 秒）
        </ElButton>
      </div>
    </ElCard>

    <PageLoading v-if="runLoading" :rows="2" chart />

    <template v-else-if="agentRun">
      <!-- 结论 -->
      <ElCard shadow="never" class="agent__result">
        <template #header>
          <div class="agent__card-head">
            <span class="agent__card-title">结论</span>
            <ElTag v-if="agentRun.status" size="small">{{ agentRun.status }}</ElTag>
            <span class="agent__grow" />
            <ElButton v-if="!agentRun.proposal_id && agentRun.status === 'DONE'"
                      type="primary" :loading="adopting" @click="adopt()">
              采纳 → 生成提案
            </ElButton>
            <ElTag v-else-if="agentRun.proposal_id" type="success" size="small">
              已采纳，提案 {{ String(agentRun.proposal_id).slice(0, 8) }}
            </ElTag>
          </div>
        </template>

        <div class="agent__verdict">
          <div class="agent__verdict-tool">
            {{ TOOL_LABEL[agentRun.result?.tool ?? ''] ?? agentRun.result?.tool ?? '—' }}
          </div>
          <div class="agent__verdict-rec">{{ agentRun.result?.recommendation ?? '—' }}</div>
          <div class="agent__verdict-meta">
            <span>置信度 {{ ((agentRun.result?.confidence ?? 0) * 100).toFixed(0) }}%</span>
            <span v-if="agentRun.result?.why_not_rule">
              · 为什么不按规则：{{ agentRun.result.why_not_rule }}
            </span>
          </div>
        </div>
      </ElCard>

      <!-- 轨迹 -->
      <ElCard shadow="never">
        <template #header>
          <span class="agent__card-title">
            轨迹（{{ agentRun.steps?.length ?? 0 }} 步）
            <span class="agent__token">
              token：{{ tokenSummary.inTok }} in / {{ tokenSummary.outTok }} out
            </span>
          </span>
        </template>

        <EmptyState v-if="!agentRun.steps?.length" compact description="没有记录到步骤" />

        <div v-else class="agent__steps">
          <div v-for="(s, i) in agentRun.steps" :key="i" class="agent__step">
            <div class="agent__step-idx">{{ s.idx ?? i + 1 }}</div>
            <div class="agent__step-body">
              <div class="agent__step-tool">
                {{ TOOL_LABEL[s.tool ?? ''] ?? s.tool ?? '（这一步没有工具）' }}
              </div>
              <div v-if="s.args && Object.keys(s.args).length" class="agent__step-args">
                <code v-for="(v, k) in s.args" :key="k" class="agent__arg">
                  {{ k }}={{ String(v).slice(0, 40) }}
                </code>
              </div>
              <div v-if="s.thought" class="agent__step-thought">{{ s.thought }}</div>
              <div v-if="s.error" class="agent__step-err">{{ s.error }}</div>
            </div>
          </div>
        </div>
      </ElCard>

      <!-- 证据编号 -->
      <ElCard v-if="agentRun.evidence?.length" shadow="never">
        <template #header><span class="agent__card-title">证据（模型引用时必须用这里的编号 ✅）</span></template>
        <div class="agent__evidence">
          <div v-for="(e, i) in agentRun.evidence" :key="i" class="agent__ev-item">
            <code class="agent__ev-ref">{{ e.id ?? e.ref ?? `E${i + 1}` }}</code>
            <span class="agent__ev-text">{{ e.text ?? e.source ?? '' }}</span>
          </div>
        </div>
      </ElCard>
    </template>

    <EmptyState v-else-if="!runLoading && !run"
                description="还没有跑过 agent"
                hint="选一个簇，点「让 agent 出方案」—— 它会给 4 步轨迹 + 一个带依据的建议"
                command="hub handle <簇id> --critic" />
  </div>
</template>

<style scoped lang="scss">
.agent {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
  }

  &__title {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__form {
    display: flex;
    align-items: center;
    gap: $space-sm;
    flex-wrap: wrap;
  }

  &__select {
    width: 320px;
  }

  &__hint {
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__alert {
    margin-top: $space-md;
  }

  &__run {
    display: flex;
    align-items: center;
    gap: $space-sm;
    margin-top: $space-md;
    padding: 8px 12px;
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);
    font-size: 12px;
  }

  &__run-meta {
    color: var(--hub-text-secondary);
  }

  &__run-err {
    color: var(--el-color-danger);
  }

  &__card-head {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  &__card-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__grow {
    flex: 1 1 auto;
  }

  &__token {
    margin-left: $space-sm;
    font-size: 11px;
    font-weight: 400;
    color: var(--hub-text-secondary);
    @include tabular-nums;
  }

  // ---------- 结论 ----------
  &__result {
    border-color: var(--el-color-primary-light-7);
    background:
      linear-gradient(135deg, var(--el-color-primary-light-9), transparent 55%),
      var(--hub-bg-card);
  }

  &__verdict {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__verdict-tool {
    font-size: 15px;
    font-weight: 600;
  }

  &__verdict-rec {
    font-size: 13px;
    line-height: 1.75;
    color: var(--hub-text-regular);
  }

  &__verdict-meta {
    display: flex;
    gap: 6px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  // ---------- 轨迹 ----------
  &__steps {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  &__step {
    display: flex;
    gap: $space-sm;
  }

  &__step-idx {
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: #fff;
    background: var(--el-color-primary);
  }

  &__step-body {
    flex: 1 1 auto;
    min-width: 0;
    padding: 8px 10px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
  }

  &__step-tool {
    font-size: 12px;
    font-weight: 600;
  }

  &__step-args {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 4px;
  }

  &__arg {
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--hub-text-secondary);
    background: var(--el-fill-color-light);
  }

  &__step-thought {
    margin-top: 4px;
    font-size: 12px;
    line-height: 1.7;
    color: var(--hub-text-regular);
  }

  &__step-err {
    margin-top: 4px;
    font-size: 11px;
    color: var(--el-color-danger);
  }

  // ---------- 证据 ----------
  &__evidence {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__ev-item {
    display: flex;
    gap: $space-sm;
    align-items: baseline;
    font-size: 12px;
  }

  &__ev-ref {
    flex: 0 0 auto;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
  }

  &__ev-text {
    color: var(--hub-text-regular);
  }
}
</style>
