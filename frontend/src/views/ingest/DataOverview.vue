<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  现有数据概览 + 开始分析（DataOverview.vue）—— 2026-11-03 新增
// ══════════════════════════════════════════════════════════════
// **解决的问题**（用户反馈）
//   "数据接入模块，库里现在有多少数据 是空的"
//      → 这里给：各表行数 + 最近一次导入 + 最近一次分析 ✅
//   "触发全链不应该在数据接口那吗？它不是危险操作"
//      → **「开始分析」就放在这里**（主按钮 ✅ 不要二次确认 ✅）
//   "链的运行状态不透明，要 Element 步骤条"
//      → 跑着的时候**本页直接显示 9 阶段步骤条** ✅
//        （同时导航栏还有一条全局胶囊，切页面也看得到 ✅）
import { computed, onMounted, ref } from 'vue'
import {
  ElAlert, ElButton, ElCard, ElMessage, ElMessageBox, ElStep, ElSteps, ElTooltip,
} from 'element-plus'
import { VideoPlay } from '@element-plus/icons-vue'

import http from '@/api/client'
import { STAGES, useAnalysisStore } from '@/stores/analysis'

interface Summary {
  rows: Record<string, number>
  latest: null | {
    file: string; platform: string | null; rows: number; kept: number
    status: string; at: string | null
  }
  analysis: null | {
    run_id: string; status: string; current_stage: string | null
    started: string | null; finished: string | null; degraded: boolean
  }
  hints: string[]
}

const analysis = useAnalysisStore()
const data = ref<Summary | null>(null)
const loading = ref(false)
const starting = ref(false)

/** 展示哪些数字（顺序 + 人话标签 ✅）*/
const CHIPS: Array<{ key: string; label: string; hint: string; unit: string }> = [
  { key: 'record_review', label: '评价', hint: '平台导出的评价条数（含噪音 ✅ 降噪在分析里做）', unit: '条' },
  { key: 'spu', label: '商品目录', hint: '跨平台对齐后的商品数', unit: '个' },
  // 2026-11-04 修：原来写的是 sales_daily 的**行数**（4,500 行）
  //   用户问「评价 7,019 为什么比销售 4,500 多？」
  //   → 因为一行 = 一个商品在某一天的汇总，不是一单
  //   → 改成显示**真实销量件数**（41 万件），行数放到小字里
  { key: 'sales_units', label: '销量', hint: '卖出多少件（日粒度汇总）', unit: '件' },
  { key: 'clusters', label: '问题簇', hint: '最近一次分析聚出来的问题数', unit: '个' },
  { key: 'proposals', label: '提案', hint: '系统生成的处置建议数', unit: '条' },
  { key: 'image_asset', label: '图片明细', hint: '图片打标的逐图记录（用于人工复核）', unit: '条' },
  { key: 'batches', label: '导入批次', hint: '历史上传了多少次', unit: '次' },
]

const chips = computed(() =>
  CHIPS.map((c) => ({ ...c, value: data.value?.rows?.[c.key] ?? 0 }))
)

const fmt = (iso: string | null): string =>
  iso ? new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }) : '—'

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data: d } = await http.get<Summary>('/api/ingest/summary')
    data.value = d
  } catch {
    data.value = null
  } finally {
    loading.value = false
  }

  // ⚠️ **自动接到"真正在跑的那条"**（2026-11-04 加）
  //    前端只认 localStorage 的 run_id ✅
  //    如果那条被后端标了 DEAD（运维清场），而库里另有活跃 run ✅
  //    → 自动切过去，避免"提示条说在跑、步骤条说失败"的自相矛盾 ✅
  const act = data.value?.analysis
  if (act && (act.status === 'QUEUED' || act.status === 'RUNNING') && act.run_id) {
    analysis.adopt(act.run_id)
  }
}

/** **开始分析**（主按钮 ✅ 不弹二次确认 —— 它是幂等的 ✅）*/
async function startAnalysis(): Promise<void> {
  starting.value = true
  try {
    const r = await analysis.start()
    if (!r.ok) {
      ElMessage.error(r.message || '提交失败')
      return
    }
    if (r.existing) {
      // **409 = 已经在跑**（不是错误 ✅）
      ElMessage.info(r.message || '已经有一条分析在跑，帮你接上了')
    } else {
      ElMessage.success('已开始分析（9 个阶段，约 8 分钟）✅ 可以去做别的事')
    }
    await load()
  } finally {
    starting.value = false
  }
}

onMounted(load)
defineExpose({ load })

// ══ **停止 / 删除任务**（2026-11-04 加）══════════════════════════
// 用户诉求："开始分析后加按钮停止任务/删除任务，**真的在后端停止和删除**"
//   停止 → POST /api/runs/{id}/cancel → status=CANCELLED → worker 在**阶段边界**退出 ✅
//   删除 → DELETE /api/runs/{id} → 删任务 + 它的簇和提案（**不动原始数据** ✅）
const stopping = ref(false)
const deleting = ref(false)

async function onStop(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '停止后，**当前正在跑的那个阶段会跑完**，然后不再推进下一个阶段。确定停止？',
      '停止分析任务',
      { type: 'warning', confirmButtonText: '停止', cancelButtonText: '继续跑' },
    )
  } catch { return }
  stopping.value = true
  try {
    const r = await analysis.cancel()
    if (r.ok) ElMessage.success(r.message || '已停止')
    else ElMessage.warning(r.message || '停不了')
  } catch (e: unknown) {
    ElMessage.error('停止失败：' + String(e))
  } finally {
    stopping.value = false
    void load()
  }
}

async function onDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除会**连同这次跑出来的簇和提案一起删掉**（原始上传的数据不动）。确定删除？',
      '删除分析任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  deleting.value = true
  try {
    const r = await analysis.remove()
    if (r.ok) ElMessage.success(r.message || '已删除')
    else ElMessage.warning(r.message || '删不了')
  } catch (e: unknown) {
    ElMessage.error('删除失败：' + String(e))
  } finally {
    deleting.value = false
    void load()
  }
}

// **跑完/失败后的下一步**（别让用户自己找 ✅）
function goDashboard(): void {
  window.location.href = '/dashboard'
}
function goOps(): void {
  window.location.href = '/ops'
}
</script>

<template>
  <ElCard shadow="never" class="dov">
    <template #header>
      <div class="dov__head">
        <div>
          <span class="dov__title">已入库文件</span>
          <span class="dov__sub">· 目前库里有什么 · 传完数据后点右边的「开始分析」</span>
        </div>
        <div class="dov__actions">
          <ElButton size="small" :loading="loading" @click="load">刷新</ElButton>
          <ElTooltip content="跑完 9 个阶段（导入→看图片→降噪→分类→对齐→聚类→比价→排序→生成建议），约 8 分钟；重复点不会跑两遍" placement="top">
            <ElButton type="primary" size="small" :icon="VideoPlay"
                      :loading="starting" :disabled="analysis.running"
                      @click="startAnalysis">
              {{ analysis.running ? '分析中…' : '开始分析' }}
            </ElButton>
          </ElTooltip>
        </div>
      </div>
    </template>

    <!-- 数字 -->
    <div class="dov__chips">
      <div v-for="c in chips" :key="c.key" class="dov__chip"
           :class="{ 'is-zero': !c.value }">
        <ElTooltip :content="c.hint" placement="top" :show-after="150">
          <div class="dov__chip-label">{{ c.label }}</div>
        </ElTooltip>
        <div class="dov__chip-value">
          {{ c.value.toLocaleString() }}<span class="dov__chip-unit">{{ c.unit }}</span>
        </div>
        <!-- 2026-11-04：销量卡下面补「多少行明细 · 多少天」
             否则用户会拿 4,500 行去和 7,019 条评价比 ✅ ✗ -->
        <div v-if="c.key === 'sales_units'" class="dov__chip-sub">
          {{ (data?.rows?.sales_daily ?? 0).toLocaleString() }} 行明细 ·
          {{ data?.rows?.sales_days ?? 0 }} 天
        </div>
      </div>
    </div>

    <!-- 最近一次导入 / 最近一次分析 -->
    <div class="dov__meta">
      <span v-if="data?.latest">
        最近导入：<b>{{ data.latest.file }}</b>
        （{{ data.latest.platform ?? '—' }} · 入库 {{ data.latest.kept }}/{{ data.latest.rows }} 行）
        · {{ fmt(data.latest.at) }}
      </span>
      <span v-else class="dov__muted">还没有导入过任何文件</span>

      <span v-if="data?.analysis">
        最近分析：<b>{{ data.analysis.status === 'DONE' ? '完成' : data.analysis.status }}</b>
        <template v-if="data.analysis.current_stage">
          · 最后阶段 {{ data.analysis.current_stage }}
        </template>
        · {{ fmt(data.analysis.finished || data.analysis.started) }}
      </span>
    </div>

    <!-- 人话提示 -->
    <ElAlert v-for="(h, i) in data?.hints ?? []" :key="i" :title="h"
             type="info" :closable="false" show-icon class="dov__hint" />

    <!-- 跑着的时候：本页直接显示 9 阶段步骤条 -->
      <div v-if="analysis.runId && (analysis.running || analysis.finished || analysis.failed)"
         class="dov__steps">
      <div class="dov__steps-head">
        <span class="dov__steps-title">
          分析进度
          <b class="dov__pct">{{ analysis.percent }}%</b>
        </span>
        <span class="dov__muted">
          <template v-if="analysis.running">
            在后台跑 —— 切页面不会中断，跑完会弹通知
          </template>
          <template v-else-if="analysis.finished">
            已完成 —— 去工作台看「今天该做什么」
          </template>
          <template v-else>执行失败 —— 去「系统日志」看跑在哪一步</template>
        </span>
      </div>
      <ElSteps :active="analysis.stepIndex" align-center class="dov__stepbar">
        <ElStep v-for="s in STAGES" :key="s.key" :title="s.title"
                :status="analysis.stageStatus(s.key)" />
      </ElSteps>
      <div v-if="analysis.errorMsg" class="dov__err">❌ {{ analysis.errorMsg }}</div>
      <!-- 停止 / 删除任务（2026-11-04 加 · 真的在后端 ✅）-->
      <div class="dov__steps-actions">
        <ElButton v-if="analysis.running" type="warning" size="small"
                  :loading="stopping" @click="onStop()">
          停止任务
        </ElButton>
        <ElButton v-if="analysis.runId" type="danger" plain size="small"
                  :loading="deleting" @click="onDelete()">
          删除任务
        </ElButton>
        <ElButton v-if="!analysis.running" type="primary" size="small"
                  :loading="starting" @click="startAnalysis()">
          {{ analysis.finished ? '再跑一次' : '重新开始分析' }}
        </ElButton>
        <ElButton v-if="!analysis.running" text size="small" @click="goOps()">
          看日志
        </ElButton>
        <ElButton v-if="analysis.finished" text size="small" @click="goDashboard()">
          去工作台看结果
        </ElButton>
      </div>
    </div>
  </ElCard>
</template>

<style scoped lang="scss">
.dov {
  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-sm;
  }

  &__title {
    font-size: 14px;
    font-weight: 600;
  }

  &__sub {
    margin-left: 4px;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__actions {
    display: flex;
    gap: $space-sm;
  }

  &__chips {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: $space-sm;

    @media (max-width: 1200px) {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  &__chip {
    padding: 8px $space-sm;
    border-radius: $radius-base;
    background: var(--hub-bg-hover, #f5f7fa);

    &.is-zero {
      opacity: 0.55;
    }
  }

  &__chip-label {
    font-size: 11px;
    color: var(--hub-text-secondary);
    cursor: help;
  }

  &__chip-value {
    margin-top: 2px;
    font-size: 18px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  &__chip-unit {
    margin-left: 2px;
    font-size: 11px;
    font-weight: 400;
    color: var(--hub-text-secondary);
  }

  &__meta {
    display: flex;
    flex-wrap: wrap;
    gap: $space-md;
    margin-top: $space-sm;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__muted {
    color: var(--hub-text-secondary);
  }

  &__hint {
    margin-top: $space-sm;
  }

  // ---------- 步骤条 ----------
  &__steps {
    margin-top: $space-md;
    padding-top: $space-md;
    border-top: 1px dashed var(--hub-border-light);
  }

  &__steps-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: $space-sm;
  }

  &__steps-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__pct {
    margin-left: 8px;
    color: var(--el-color-primary);
    font-variant-numeric: tabular-nums;
  }

  &__steps-actions {
    display: flex;
    gap: $space-sm;
    margin-top: $space-sm;
  }

  &__stepbar {
    // 9 个阶段横排：小屏会挤 → 允许横向滚动 ✅
    overflow-x: auto;

    :deep(.el-step__title) {
      font-size: 12px;
    }

    /* **进行中：蓝色 + 呼吸闪烁** ✅ */
    :deep(.el-step__head.is-process) {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary);
      animation: dov-breathe 1.6s ease-in-out infinite;
    }

    /* **已完成：绿色** ✅ */
    :deep(.el-step__head.is-success) {
      color: var(--el-color-success);
      border-color: var(--el-color-success);
    }

    :deep(.el-step__title.is-success) {
      color: var(--el-color-success);
    }
  }

  &__err {
    margin-top: $space-sm;
    padding: 6px 8px;
    font-size: 12px;
    color: var(--el-color-danger);
    background: var(--el-color-danger-light-9);
    border-radius: $radius-sm;
  }
}
@keyframes dov-breathe {
  0%, 100% {
    opacity: 1;
  }

  50% {
    opacity: 0.6;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dov__stepbar :deep(.el-step__head.is-process) {
    animation: none;
  }
}
.dov {
  &__chip-sub {
    margin-top: 2px;
    font-size: 11px;
    color: var(--hub-text-muted);
  }
}
</style>
