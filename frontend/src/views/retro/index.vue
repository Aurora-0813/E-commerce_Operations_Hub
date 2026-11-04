<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  4 号页：复盘报告（DID）
//  数据源：GET /api/retro（`{items:[...]}`）、POST /api/retro、GET /api/export/retro
//  ⚠️ **现在 effect/conclusion 全是 null** —— 真实干预还在观察窗口（最早 2026-11-13）✅
//     → 页面必须能显示"还没到窗口"，而不是空白/0 ✅
//  ⚠️ 置信区间：`/api/retro` 现在只给 `effect` + `confidence`，
//     **没给 ci_low / ci_high** ✅ → 所以区间图**暂时画不了**
//     → 我的处理：**effect 是数字才画点，有 ci 才画误差棒**（两种都能吃 ✅）
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref } from 'vue'
import {
  ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElMessage,
} from 'element-plus'
import { Download, Refresh } from '@element-plus/icons-vue'

import { getRetro, type RetroItem } from '@/api/data'
import { downloadExport, rerunRetro } from '@/api/write'
import EmptyState from '@/components/EmptyState.vue'
import LabelTag from '@/components/LabelTag.vue'
import { CONCLUSION, REASON, STATUS } from '@/config/labels'
import ErrorState from '@/components/ErrorState.vue'
import PageLoading from '@/components/PageLoading.vue'

const guideClosed = ref(false)
const loading = ref(true)
const error = ref<unknown>(null)
const items = ref<RetroItem[]>([])
const running = ref(false)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const r = await getRetro(200)
    items.value = r.items
    total.value = r.total
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

async function doRerun(): Promise<void> {
  running.value = true
  try {
    const r = await rerunRetro(true)
    ElMessage.success(`复盘完成：${r.done} 组有结论，${r.skipped} 组跳过`)
    await load()
  } finally {
    running.value = false
  }
}

async function doExport(): Promise<void> {
  const { blob, rows, piiRedacted } = await downloadExport('retro')
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'retro.xlsx'
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success(`已导出 ${rows} 行${piiRedacted ? `（脱敏 ${piiRedacted} 处）` : ''}`)
}

// ---------- 汇总 ----------
// ⚠️ **用后端返回的 total**（2026-11-02 修）：`items` 只有 limit 条 ✅
//    原来用 items.length → 531 个等窗口被显示成 50 ✅ ✗
const total = ref(0)

const counts = computed(() => {
  const c = { POSITIVE: 0, NEGATIVE: 0, INCONCLUSIVE: 0, pending: 0 }
  for (const it of items.value) {
    if (it.conclusion === 'POSITIVE') c.POSITIVE += 1
    else if (it.conclusion === 'NEGATIVE') c.NEGATIVE += 1
    else if (it.conclusion === 'INCONCLUSIVE') c.INCONCLUSIVE += 1
    else c.pending += 1
  }
  return c
})

const concluded = computed(() =>
  items.value.filter((i) => i.conclusion !== null),
)

/**
 * 区间"跨不跨 0"——**这是页面最重要的一句话** ✅
 * ⚠️ 后端现在没给 ci_low/ci_high → 只要 effect 缺失就判"还不能下结论" ✅
 */

/** effect 可能是数字、可能是 null、以后可能是对象（带 ci）→ 统一取值 ✅ */
function effectNum(it: RetroItem): number | null {
  const e: unknown = it.effect
  if (typeof e === 'number') return e
  if (e && typeof e === 'object') {
    const o = e as Record<string, unknown>
    const v = o.point ?? o.value ?? o.effect
    if (typeof v === 'number') return v
  }
  return null
}
function fmtPct(v: number | null): string {
  if (v === null) return '—'
  return `${(v * 100).toFixed(2)}pp`
}
</script>

<template>
  <div class="retro">
    <div class="retro__head">
      <div>
        <h2 class="retro__title">复盘报告</h2>
        <div class="retro__sub">
          方法和判据：<b>双重差分（DID）+ bootstrap 区间</b> —— 区间跨 0 就不下结论
        </div>
      </div>
      <div class="retro__actions">
        <ElButton :icon="Refresh" :loading="running" @click="doRerun">重新复盘</ElButton>
        <ElButton :icon="Download" @click="doExport">导出 xlsx</ElButton>
      </div>
    </div>

        <!-- ══════════════════════════════════════════════════════════
         为什么大部分动作"还没有结论"（2026-11-03 加，C）
         ══════════════════════════════════════════════════════════ -->
    <ElCard v-if="!guideClosed" shadow="never" class="retro__guide">
      <template #header>
        <div class="retro__guide-head">
          <span class="retro__guide-title">为什么大部分动作"还没有结论"</span>
          <ElButton text size="small" @click="guideClosed = true">知道了</ElButton>
        </div>
      </template>
      <div class="retro__guide-body">
        <p>
          每个动作做完后要等 <b>14 天观察窗口</b> 才判断有没有用 ——
          效果不会当天显现，现在算是噪音。
        </p>
        <p>
          窗口到了之后用 <b>DID（双重差分）</b> 算：
          <code>效果 = 处理组前后变化 − 对照组前后变化</code>，再给一个 <b>95% 置信区间</b>。
        </p>
        <p>
          <b>区间跨 0 → 不能下结论</b>（可能是巧合）。
          所以我们宁可显示「等观察窗口」，也<b>不在窗口没到时宣布成功</b>
        </p>
        <p class="retro__guide-muted">
          当前：<b>{{ counts.POSITIVE + counts.NEGATIVE + counts.INCONCLUSIVE }}</b> 条已有结论 ·
          <b>{{ total || counts.pending }}</b> 条还在等窗口
        </p>
      </div>
    </ElCard>

<PageLoading v-if="loading && !items.length" :rows="3" />
    <ErrorState v-else-if="error" :error="error" @retry="load" />

    <template v-else>
      <!-- 汇总四卡 -->
      <div class="retro__kpis">
        <div class="retro__kpi">
          <div class="retro__kpi-label">有效</div>
          <div class="retro__kpi-value is-ok">{{ counts.POSITIVE }}</div>
        </div>
        <div class="retro__kpi">
          <div class="retro__kpi-label">有害</div>
          <div class="retro__kpi-value is-bad">{{ counts.NEGATIVE }}</div>
        </div>
        <div class="retro__kpi">
          <div class="retro__kpi-label">不能下结论</div>
          <div class="retro__kpi-value is-warn">{{ counts.INCONCLUSIVE }}</div>
        </div>
        <div class="retro__kpi">
          <div class="retro__kpi-label">等观察窗口</div>
          <div class="retro__kpi-value">{{ total || counts.pending }}</div>
        </div>
      </div>

      <!-- ⚠️ 现在的真实状态：全是 pending -->
      <ElCard v-if="!concluded.length" shadow="never">
        <EmptyState
          description="现在还没有能下结论的动作"
          :hint="`${counts.pending} 个动作还在观察窗口里。窗口到期后 ` + '→' + ` 自动复盘，最早 2026-11-13 出结果。`"
          command="hub retro"
        />
        <div class="retro__why">
          <div class="retro__why-title">为什么不现在就下结论？</div>
          <ul>
            <li>动作刚执行完，<b>效果还没显现</b>；现在算 DID 等于用噪音判业务效果</li>
            <li>我们的判据是<b>区间不跨 0</b>；窗口没到，区间必然很宽 → 只会得到 INCONCLUSIVE</li>
            <li>宁可显示"还没到"，也不拿一个假数字交差</li>
          </ul>
        </div>
      </ElCard>

      <!-- 有结论的才画区间图 -->
      <ElCard v-if="concluded.length" shadow="never">
        <template #header>
          <span class="retro__card-title">DID 效果量（点 = 点估计；区间不跨 0 才算数）</span>
        </template>
        <div class="retro__bars">
          <div v-for="it in concluded" :key="it.proposal_id" class="retro__bar-row">
            <div class="retro__bar-label">
              <code>{{ it.proposal_id.slice(0, 8) }}</code>
              <LabelTag :dict="CONCLUSION" :value="it.conclusion" fallback="等观察窗口" />
            </div>
            <div class="retro__bar-track">
              <!-- 0 线 -->
              <div class="retro__zero" />
              <!-- 效果量点 -->
              <div
                v-if="effectNum(it) !== null"
                class="retro__dot"
                :class="{ 'is-ok': (effectNum(it) ?? 0) < 0, 'is-bad': (effectNum(it) ?? 0) > 0 }"
                :style="{ left: `${Math.min(96, Math.max(4, 50 + (effectNum(it) ?? 0) * 300))}%` }"
                :title="fmtPct(effectNum(it))"
              />
            </div>
            <div class="retro__bar-value">{{ fmtPct(effectNum(it)) }}</div>
          </div>
        </div>
        <div class="retro__legend">
          ⚠️ 现在接口只返回<b>点估计</b> + <code>confidence</code>，没有 <code>ci_low</code> /
          <code>ci_high</code> → 所以画不出误差棒。
          等后端把区间返回了，这排就能加上（脚本已经支持两种形状）。
        </div>
      </ElCard>

      <!-- 明细表 -->
      <ElCard shadow="never">
        <template #header><span class="retro__card-title">全部动作（{{ items.length }} 条）</span></template>
        <ElDescriptions v-for="it in items.slice(0, 20)" :key="it.proposal_id"
                        :column="3" size="small" border class="retro__detail">
          <ElDescriptionsItem label="提案">
            <code>{{ it.proposal_id.slice(0, 8) }}</code>
          </ElDescriptionsItem>
          <ElDescriptionsItem label="结论">
            <LabelTag :dict="CONCLUSION" :value="it.conclusion" fallback="等观察窗口" />
          </ElDescriptionsItem>
          <ElDescriptionsItem label="效果量">{{ fmtPct(effectNum(it)) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="状态">
            <LabelTag :dict="STATUS" :value="it.status" />
          </ElDescriptionsItem>
          <ElDescriptionsItem label="观察期止">{{ it.observe_end ?? '—' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="为什么是这个结论">
            <LabelTag :dict="REASON" :value="it.reason_code" fallback="还没算" />
          </ElDescriptionsItem>
        </ElDescriptions>
      </ElCard>
    </template>
  </div>
</template>

<style scoped lang="scss">
.retro {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-md;
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

  &__actions {
    display: flex;
    gap: $space-sm;
  }

  &__kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: $space-md;
  }

  &__guide {
    border-color: var(--el-color-primary-light-7);
  }

  &__guide-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  &__guide-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__guide-body {
    font-size: 12px;
    line-height: 1.8;
    color: var(--hub-text-secondary);

    p {
      margin: 0 0 6px;
    }

    b {
      color: var(--hub-text-primary);
    }

    code {
      padding: 0 4px;
      border-radius: 3px;
      background: var(--hub-bg-hover, #f5f7fa);
      font-size: 11px;
    }
  }

  &__guide-muted {
    margin-top: $space-sm !important;
    padding-top: $space-sm;
    border-top: 1px dashed var(--hub-border-light);
  }

  &__kpi {
    @include card;
    padding: $space-md;
  }

  &__kpi-label {
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__kpi-value {
    margin-top: 4px;
    font-size: 24px;
    font-weight: 600;
    @include tabular-nums;

    &.is-ok { color: var(--el-color-success); }
    &.is-bad { color: var(--el-color-danger); }
    &.is-warn { color: var(--el-color-warning); }
  }

  &__card-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__why {
    margin-top: $space-md;
    padding: $space-md;
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);

    ul {
      margin: 6px 0 0;
      padding-left: 18px;
      font-size: 12px;
      line-height: 1.9;
      color: var(--hub-text-regular);
    }
  }

  &__why-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--hub-text-primary);
  }

  // ---------- 效果量条 ----------
  &__bars {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  &__bar-row {
    display: grid;
    grid-template-columns: 210px 1fr 90px;
    align-items: center;
    gap: $space-sm;
  }

  &__bar-label {
    display: flex;
    align-items: center;
    gap: 6px;

    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      color: var(--hub-text-secondary);
    }
  }

  &__bar-track {
    position: relative;
    height: 18px;
    border-radius: 9px;
    background: var(--el-fill-color-lighter);
  }

  &__zero {
    position: absolute;
    left: 50%;
    top: -2px;
    bottom: -2px;
    width: 1px;
    background: var(--hub-border);
  }

  &__dot {
    position: absolute;
    top: 50%;
    width: 10px;
    height: 10px;
    margin-left: -5px;
    border-radius: 50%;
    transform: translateY(-50%);
    background: var(--el-color-primary);

    &.is-ok { background: var(--el-color-success); }
    &.is-bad { background: var(--el-color-danger); }
  }

  &__bar-value {
    text-align: right;
    font-size: 12px;
    @include tabular-nums;
  }

  &__legend {
    margin-top: $space-md;
    font-size: 11px;
    line-height: 1.7;
    color: var(--hub-text-secondary);

    code {
      padding: 1px 3px;
      border-radius: 3px;
      background: var(--el-fill-color-light);
    }
  }

  &__detail {
    margin-bottom: $space-sm;
  }

  @media (max-width: 1100px) {
    &__kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    &__bar-row {
      grid-template-columns: 1fr;
    }
  }
}
</style>
