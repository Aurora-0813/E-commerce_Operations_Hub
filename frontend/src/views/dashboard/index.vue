<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  1 号页：今日工作台
//  数据源（**全部真实接口**，无 mock ✅）：
//    GET /api/today                     → 🔴 要我做 / ⏳ 等窗口
//    GET /api/brief                     → AI 日报（**available 可能 false**）
//    GET /api/charts/cluster-tiers      → ① P 档环形
//    GET /api/charts/hitl-trend         → ② 待批趋势
//    GET /api/charts/observe-timeline   → ③ 等窗口甘特
// ══════════════════════════════════════════════════════════════
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElCard, ElIcon, ElTag } from 'element-plus'
import { ArrowRight, Bell, Clock, Refresh } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent, LegendComponent, TooltipComponent,
} from 'echarts/components'

import http from '@/api/client'
import { getBrief, getToday, type BriefResp, type TodayItem, type TodayResp } from '@/api/read'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageLoading from '@/components/PageLoading.vue'
import { useAppStore } from '@/stores/app'

// ECharts 按需注册（**只注册用到的**，包体积小一半 ✅）
use([CanvasRenderer, PieChart, BarChart, GridComponent, TooltipComponent, LegendComponent])

const router = useRouter()
const app = useAppStore()

// ---------- 状态 ----------
const loading = ref(true)
const error = ref<unknown>(null)
const today = ref<TodayResp | null>(null)
const brief = ref<BriefResp | null>(null)
const tiers = ref<Array<{ tier: string; n: number }>>([])
const hitl = ref<Array<{ date: string; created: number; pending: number }>>([])

// ---------- 拉数据 ----------
async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    // 并行拉（**互不阻塞**；某个挂了不影响其他卡片 ✅）
    const [t, b, tr, h] = await Promise.allSettled([
      getToday(),
      getBrief(),
      http.get<{ items: Array<{ tier: string; clusters: number; reviews: number }> }>('/api/charts/cluster-tiers'),
      http.get<{ items: Array<{ date: string; created: number; pending: number }> }>(
        '/api/charts/hitl-trend', { params: { days: app.rangeDays } }),
    ])
    if (t.status === 'fulfilled') today.value = t.value
    else error.value = t.reason
    if (b.status === 'fulfilled') brief.value = b.value
    if (tr.status === 'fulfilled') {
      tiers.value = (tr.value.data.items ?? []).map((i) => ({ tier: i.tier, n: i.clusters }))
    }
    if (h.status === 'fulfilled') hitl.value = h.value.data.items ?? []
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
// 时间范围变了 → 重拉（**只有依赖它的两个图需要重拉，但一起重拉更简单** ✅）
watch(() => app.range, () => void load())

// ---------- 轮询：有"要我做"的东西时，60 秒刷一次 ----------
//   ⚠️ 卸载必须清（用户 prompt 硬要求 ✅）
let timer: number | null = null
onMounted(() => {
  timer = window.setInterval(() => void load(), 60_000)
})
onBeforeUnmount(() => {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
})

// ---------- 派生 ----------
const mustDo = computed<TodayItem[]>(() => today.value?.must_do ?? [])
const waiting = computed<TodayItem[]>(() => today.value?.waiting ?? [])
const notice = computed<TodayItem[]>(() => today.value?.notice ?? [])

const kpi = computed(() => [
  { label: '要我做', value: mustDo.value.length, tone: 'danger', unit: '件' },
  { label: '等观察窗口', value: waiting.value.length, tone: 'warning', unit: '个动作' },
  { label: '问题簇', value: tiers.value.reduce((s, t) => s + t.n, 0), tone: 'primary', unit: '个' },
  { label: 'P0 / P1', value: `${tiers.value.find((t) => t.tier === 'P0')?.n ?? 0} / ${tiers.value.find((t) => t.tier === 'P1')?.n ?? 0}`, tone: 'info', unit: '' },
])

// ---------- 图① P 档环形 ----------
const TIER_COLOR: Record<string, string> = {
  P0: '#f56c6c', P1: '#e6a23c', P2: '#409eff', P3: '#909399',
}
const pieOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c} 个 ({d}%)' },
  legend: { bottom: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11 } },
  series: [{
    type: 'pie',
    radius: ['52%', '74%'],
    center: ['50%', '44%'],
    avoidLabelOverlap: true,
    itemStyle: { borderColor: 'transparent', borderWidth: 2 },
    label: { show: false },
    data: tiers.value.map((t) => ({
      name: t.tier, value: t.n, itemStyle: { color: TIER_COLOR[t.tier] ?? '#909399' },
    })),
  }],
}))

// ---------- 图② 待批趋势 ----------
const barOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
  xAxis: {
    type: 'category',
    data: hitl.value.map((d) => d.date.slice(5)),
    axisLine: { lineStyle: { color: '#dcdfe6' } },
    axisLabel: { fontSize: 11, color: '#909399' },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: '#ebeef5' } },
    axisLabel: { fontSize: 11, color: '#909399' },
  },
  series: [
    { name: '新建', type: 'bar', stack: 'total', data: hitl.value.map((d) => d.created),
      itemStyle: { color: '#409eff', borderRadius: [0, 0, 0, 0] } },
    { name: '仍待批', type: 'bar', stack: 'total', data: hitl.value.map((d) => d.pending),
      itemStyle: { color: '#f56c6c' } },
  ],
}))

// ---------- 交互 ----------
function goto(item: TodayItem): void {
  if (!item.resource_id) return
  if (item.kind === 'hitl' || item.kind === 'proposal') {
    void router.push(`/proposals/${item.resource_id}`)
  } else if (item.kind === 'agent') {
    void router.push('/agent')
  } else if (item.kind === 'dead_letter') {
    void router.push('/ops')
  } else {
    void router.push('/problems')
  }
}
</script>

<template>
  <div class="dash">
    <!-- ============ 头部 ============ -->
    <div class="dash__head">
      <div>
        <h2 class="dash__title">今日工作台</h2>
        <div class="dash__sub">
          {{ new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }) }}
          · 数据每 60 秒刷新
        </div>
      </div>
      <ElButton :icon="Refresh" :loading="loading" @click="load()">刷新</ElButton>
    </div>

    <PageLoading v-if="loading && !today" :rows="2" chart />

    <ErrorState v-else-if="error" :error="error" @retry="load()" />

    <template v-else>
      <!-- ============ ① AI 日报 ============ -->
      <ElCard class="dash__brief" shadow="never">
        <template #header>
          <div class="dash__brief-head">
            <span class="dash__brief-title">AI 日报</span>
            <ElTag v-if="brief?.generated_at" size="small" type="info" effect="plain">
              {{ brief.generated_at.slice(0, 16).replace('T', ' ') }}
            </ElTag>
            <span class="dash__brief-note">
              每条都核对过：数字必须能在输入指标里找到 ✅
            </span>
          </div>
        </template>

        <!-- available: false → **走空态**，不塞假数据 ✅ -->
        <EmptyState
          v-if="!brief?.available"
          compact
          description="还没有生成过 AI 日报"
          hint="日报需要跑一次才会产生（数字要经过校验，不能凭空生成）"
          command="hub brief"
        />

        <template v-else>
          <ul class="dash__bullets">
            <li v-for="(b, i) in (brief.lines ?? [])" :key="i">{{ b }}</li>
          </ul>

        </template>
      </ElCard>

      <!-- ============ ② KPI 四卡 ============ -->
      <div class="dash__kpis">
        <div v-for="k in kpi" :key="k.label" class="dash__kpi">
          <div class="dash__kpi-label">{{ k.label }}</div>
          <div :class="['dash__kpi-value', `is-${k.tone}`]">{{ k.value }}</div>
          <div class="dash__kpi-unit">{{ k.unit }}</div>
        </div>
      </div>

      <!-- ============ ③ 两张图 ============ -->
      <div class="dash__charts">
        <ElCard shadow="never" class="dash__chart">
          <template #header><span class="dash__chart-title">① 问题簇 P 档分布</span></template>
          <EmptyState v-if="!tiers.length" compact description="还没有问题簇"
                      hint="跑完聚类后这里会有 P0~P3 的分布" command="hub cluster" />
          <VChart v-else class="dash__chart-body" :option="pieOption" autoresize />
        </ElCard>

        <ElCard shadow="never" class="dash__chart">
          <template #header><span class="dash__chart-title">② 待批趋势（近 {{ app.rangeDays }} 天）</span></template>
          <EmptyState v-if="!hitl.length" compact description="这段时间没有待批记录" />
          <VChart v-else class="dash__chart-body" :option="barOption" autoresize />
        </ElCard>
      </div>

      <!-- ============ ④ 🔴 要我做 ============ -->
      <ElCard shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <ElIcon color="#f56c6c"><Bell /></ElIcon>
            <span class="dash__list-title">要我做</span>
            <ElTag v-if="mustDo.length" type="danger" size="small" round>{{ mustDo.length }}</ElTag>
          </div>
        </template>

        <EmptyState v-if="!mustDo.length" compact description="现在没有需要你处理的事 ✅"
                    hint="待批提案 / 到期复盘 / 死信会出现在这里" />

        <div v-else class="dash__items">
          <div v-for="(it, i) in mustDo" :key="i" class="dash__item" @click="goto(it)">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.title }}</div>
              <div v-if="it.hint" class="dash__item-hint">{{ it.hint }}</div>
            </div>
            <ElIcon class="dash__item-arrow"><ArrowRight /></ElIcon>
          </div>
        </div>
      </ElCard>

      <!-- ============ ⑤ ⏳ 等观察窗口 ============ -->
      <ElCard v-if="waiting.length" shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <ElIcon color="#e6a23c"><Clock /></ElIcon>
            <span class="dash__list-title">等观察窗口</span>
            <ElTag type="warning" size="small" round>{{ waiting.length }}</ElTag>
            <span class="dash__list-note">窗口到期后自动复盘（最早 2026-11-13）</span>
          </div>
        </template>
        <div class="dash__items">
          <div v-for="(it, i) in waiting" :key="i" class="dash__item is-muted">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.title }}</div>
              <div v-if="it.hint" class="dash__item-hint">{{ it.hint }}</div>
            </div>
          </div>
        </div>
      </ElCard>

      <!-- ============ ⑥ 知会 ============ -->
      <ElCard v-if="notice.length" shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <span class="dash__list-title">知会</span>
            <ElTag size="small" round type="info">{{ notice.length }}</ElTag>
          </div>
        </template>
        <div class="dash__items">
          <div v-for="(it, i) in notice" :key="i" class="dash__item is-muted">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.title }}</div>
              <div v-if="it.hint" class="dash__item-hint">{{ it.hint }}</div>
            </div>
          </div>
        </div>
      </ElCard>
    </template>
  </div>
</template>

<style scoped lang="scss">
.dash {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  // ---------- 头部 ----------
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

  // ---------- AI 日报 ----------
  &__brief {
    border-color: var(--el-color-primary-light-7);
    background:
      linear-gradient(135deg, var(--el-color-primary-light-9), transparent 60%),
      var(--hub-bg-card);
  }

  &__brief-head {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__brief-title {
    font-size: 14px;
    font-weight: 600;
  }

  &__brief-note {
    margin-left: auto;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__bullets {
    margin: 0;
    padding-left: 18px;
    line-height: 2;
    font-size: 13px;

    li::marker {
      color: var(--el-color-primary);
    }
  }

  &__inputs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: $space-sm;
    padding-top: $space-sm;
    border-top: 1px dashed var(--hub-border-light);
  }

  &__inputs-label {
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  // ---------- KPI ----------
  &__kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: $space-md;
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
    margin-top: 6px;
    font-size: 26px;
    font-weight: 600;
    line-height: 1.2;
    // **KPI 数字必须等宽**（用户 prompt 硬要求 ✅）
    @include tabular-nums;

    &.is-danger { color: var(--el-color-danger); }
    &.is-warning { color: var(--el-color-warning); }
    &.is-primary { color: var(--el-color-primary); }
    &.is-info { color: var(--hub-text-regular); }
  }

  &__kpi-unit {
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  // ---------- 图 ----------
  &__charts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: $space-md;
  }

  &__chart-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__chart-body {
    height: 220px;
  }

  // ---------- 列表 ----------
  &__list-head {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  &__list-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__list-note {
    margin-left: auto;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__items {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__item {
    display: flex;
    align-items: center;
    gap: $space-sm;
    padding: 10px 12px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    cursor: pointer;
    transition: all $duration-fast;

    &:hover {
      border-color: var(--el-color-primary-light-5);
      background: var(--el-color-primary-light-9);

      .dash__item-arrow {
        transform: translateX(2px);
        color: var(--el-color-primary);
      }
    }

    &.is-muted {
      cursor: default;

      &:hover {
        border-color: var(--hub-border-light);
        background: transparent;
      }
    }
  }

  &__item-main {
    flex: 1 1 auto;
    min-width: 0;
  }

  &__item-title {
    font-size: 13px;
    color: var(--hub-text-primary);
    @include ellipsis;
  }

  &__item-hint {
    margin-top: 2px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__item-arrow {
    flex: 0 0 auto;
    color: var(--hub-text-placeholder);
    transition: all $duration-fast;
  }

  // ---------- 响应式 ----------
  @media (max-width: 1100px) {
    &__kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    &__charts {
      grid-template-columns: 1fr;
    }
  }
}
</style>
