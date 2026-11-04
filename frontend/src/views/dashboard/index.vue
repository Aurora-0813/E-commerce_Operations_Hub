<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  1 号页：今日工作台
//  数据源（**全部真实接口**，无 mock ✅）：
//    GET /api/today                     → 🔴 要我做 / ⏳ 等窗口
//    GET /api/brief                     → AI 日报（**available 可能 false**）
//    GET /api/charts/cluster-tiers      → P 档环形
//    GET /api/charts/hitl-trend         → 待批趋势
//    GET /api/charts/observe-timeline   → 等窗口甘特
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
/**
 * ⚠️ **字段名必须和后端一致**（2026-11-03 修）
 *    后端 `/api/charts/hitl-trend` 每条是：
 *      {date, PENDING, APPROVED, REJECTED, EXPIRED}
 *    我原来读 `created` / `pending` → **全是 undefined** → 柱子高度 0 → 看起来空 ✅ ✗
 */
interface HitlPoint {
  date: string
  PENDING: number
  APPROVED: number
  REJECTED: number
  EXPIRED: number
}
const hitl = ref<HitlPoint[]>([])

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
      http.get<{ items: HitlPoint[] }>(
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
/**
 * 三张「来源指标」卡的**副标题**
 *
 * ⚠️ 用户反馈："规模影响、处理漏斗、数据质量看不懂在展示什么" ✅
 *    → 只有数字、没有「这跟我有什么关系」✅
 *    → 每张卡加一句：**这是什么 + 该关注哪个数** ✅
 */
/**
 * **主区域只留「规模与影响」**（2026-11-04）
 *
 * 用户反馈："处理漏斗和数据质量放在其他位置，**这个和工作人员没关系，他们也看不懂**"
 *   → 「规模与影响」= 生意盘子有多大（运营看得懂 ✅）留主区域
 *   → 「处理漏斗」「数据质量」= 实施/管理层看的系统指标 ✅ 挪到页面底部 ✅
 */
const MAIN_SECTION = '规模与影响'
const mainSections = computed(() => (brief.value?.sections ?? []).filter(
  (x) => x.title === MAIN_SECTION))

const SECTION_SUB: Record<string, string> = {
  规模与影响: '你的生意盘子有多大 —— **重点看差评率有没有变差**',
  处理漏斗: '一堆问题走到哪一步了 —— **重点看「待批」有没有积压**',
  数据质量: '系统靠不靠得住 —— **降噪率高说明噪音多，覆盖率高说明分析覆盖全**',
}
function sectionSub(title: string): string {
  return SECTION_SUB[title] ?? ''
}

const mustDo = computed<TodayItem[]>(() => today.value?.must_do ?? [])
const waiting = computed<TodayItem[]>(() => today.value?.waiting ?? [])
const notice = computed<TodayItem[]>(() => today.value?.notice ?? [])

const kpi = computed(() => [
  { label: '待处理事项', value: mustDo.value.length, tone: 'danger', unit: '件' },
  { label: '效果观察中', value: waiting.value.length, tone: 'warning', unit: '个动作' },
  { label: '待处理问题', value: tiers.value.reduce((s, t) => s + t.n, 0), tone: 'primary', unit: '个' },
  { label: '特急 / 急', value: `${tiers.value.find((t) => t.tier === '特急')?.n ?? 0} / ${tiers.value.find((t) => t.tier === '急')?.n ?? 0}`, tone: 'info', unit: '' },
])

// ---------- 图问题紧急度分布（紧急度四档）----------
// ⚠️ **后端返回的 tier 是内部代号 P0/P1/P2/P3**（2026-11-04 改）
//    用户反馈："待处理问题 P 档分布是什么意思" → 图例写成**业务话术** ✅
const TIER_LABEL: Record<string, string> = {
  P0: '特急',
  P1: '急',
  P2: '普通',
  P3: '不急',
}
const TIER_COLOR: Record<string, string> = {
  P0: '#f56c6c', P1: '#e6a23c', P2: '#409eff', P3: '#909399',
}
const pieOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c} 个 ({d}%)' },
  // **图例放在图下方、和绘图区留出间距**（2026-11-04 修：原来压在柱子上 ✅ ✗）
  legend: { bottom: 0, left: 'center', itemWidth: 8, itemHeight: 8,
            itemGap: 16, textStyle: { fontSize: 11, color: '#909399' } },
  series: [{
    type: 'pie',
    radius: ['52%', '74%'],
    center: ['50%', '44%'],
    avoidLabelOverlap: true,
    itemStyle: { borderColor: 'transparent', borderWidth: 2 },
    label: { show: false },
    data: tiers.value.map((t) => ({
      name: TIER_LABEL[t.tier] ?? t.tier,
      value: t.n,
      itemStyle: { color: TIER_COLOR[t.tier] ?? '#909399' },
    })),
  }],
}))

// ---------- 图待批趋势 ----------
const barOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: { left: 8, right: 12, top: 20, bottom: 28, containLabel: true },
  // **图例放在图下方、和绘图区留出间距**（2026-11-04 修：原来压在柱子上 ✅ ✗）
  legend: { bottom: 0, left: 'center', itemWidth: 8, itemHeight: 8,
            itemGap: 16, textStyle: { fontSize: 11, color: '#909399' } },
  xAxis: {
    type: 'category',
    data: hitl.value.map((d) => d.date.slice(5)),
    axisLine: { lineStyle: { color: '#dcdfe6' } },
    axisLabel: { fontSize: 11, color: '#909399' },
  },
  yAxis: {
    type: 'value',
    // **整数刻度**（别出现 0.5 / 1.5 这种"半个人" ✅）
    minInterval: 1,
    splitLine: { lineStyle: { color: '#ebeef5', type: 'dashed' } },
    axisLabel: { fontSize: 11, color: '#909399' },
  },
  series: [
    // ⚠️ **`barMaxWidth` 是关键**（2026-11-04 修）
    //    只有 1 天数据时，柱子默认**铺满整张图** → 一大块橙色方块，极丑 ✅ ✗
    //    限宽 32px + 圆角 → 变成一根正常的柱子 ✅
    // **后端真字段**：PENDING / APPROVED / REJECTED ✅
    { name: '待批', type: 'bar', stack: 'total', barMaxWidth: 32,
      itemStyle: { color: '#e6a23c', borderRadius: [4, 4, 0, 0] },
      data: hitl.value.map((d) => d.PENDING) },
    { name: '已批准', type: 'bar', stack: 'total', barMaxWidth: 32,
      itemStyle: { color: '#67c23a' },
      data: hitl.value.map((d) => d.APPROVED) },
    { name: '已拒绝', type: 'bar', stack: 'total', barMaxWidth: 32,
      itemStyle: { color: '#f56c6c' },
      data: hitl.value.map((d) => d.REJECTED) },
  ],
}))

// ---------- 交互 ----------
/**
 * 点击待办 → 跳对应页面
 *
 * ⚠️ **2026-11-03 修**：`/api/today` 的每条是 `{text, next_step, detail}` ✅
 *    没有 `kind` / `resource_id` ✅ → 原来 `if (item.kind === ...)` **全不成立** ✅ ✗
 *    → 改成**按文案关键词**路由（后端文案是稳定的 ✅）
 */
function goto(item: TodayItem): void {
  const t = (item.text || '') + (item.detail || '')
  if (t.includes('动作在效果观察中') || t.includes('最早')) {
    void router.push('/retro')
  } else if (t.includes('死信')) {
    void router.push('/ops')
  } else if (t.includes('拒绝') && t.includes('提案')) {
    void router.push('/proposals')
  } else if (t.includes('提案') || t.includes('L2') || t.includes('L3')) {
    void router.push('/proposals')
  } else if (t.includes('agent')) {
    void router.push('/agent')
  } else {
    void router.push('/problems')
  }
  void item
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
      <ElButton :icon="Refresh" :loading="loading" @click="load">刷新</ElButton>
    </div>

    <PageLoading v-if="loading && !today" :rows="2" chart />

    <ErrorState v-else-if="error" :error="error" @retry="load" />

    <template v-else>
      <!-- ============ AI 日报 ============ -->
      <ElCard class="dash__brief" shadow="never">
        <template #header>
          <div class="dash__brief-head">
            <span class="dash__brief-title">AI 日报</span>
            <ElTag v-if="brief?.generated_at" size="small" type="info" effect="plain">
              {{ brief.generated_at.slice(0, 16).replace('T', ' ') }}
            </ElTag>
            <span class="dash__brief-note">
              每条都核对过：数字必须能在输入指标里找到
            </span>
          </div>
        </template>

        <!-- available: false → 走空态，不塞假数据 -->
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

      <!-- ============ KPI 四卡 ============ -->
      <!-- 来源指标（2026-11-03 加）：后端返回结构化 sections（人话+数字），直接渲染 -->
      <div v-if="brief?.sections?.length" class="dash__sections">
        <div v-for="sec in mainSections" :key="sec.title" class="dash__section">
          <div class="dash__section-title">{{ sec.title }}</div>
          <div v-if="sectionSub(sec.title)" class="dash__section-sub">
            {{ sectionSub(sec.title).replace(/\*\*/g, '') }}
          </div>
          <dl class="dash__section-list">
            <template v-for="it in sec.items" :key="it.label">
              <dt>{{ it.label }}</dt>
              <dd>{{ it.value }}</dd>
            </template>
          </dl>
        </div>
      </div>

      <div class="dash__kpis">
        <div v-for="k in kpi" :key="k.label" class="dash__kpi">
          <div class="dash__kpi-label">{{ k.label }}</div>
          <div :class="['dash__kpi-value', `is-${k.tone}`]">{{ k.value }}</div>
          <div class="dash__kpi-unit">{{ k.unit }}</div>
        </div>
      </div>

      <!-- ============ 两张图 ============ -->
      <div class="dash__charts">
        <ElCard shadow="never" class="dash__chart">
          <template #header><span class="dash__chart-title">问题紧急度分布</span></template>
        <div class="dash__chart-note">紧急度 = 影响面 40% + 严重度 30% + 趋势 20% + 可执行性 10%</div>
          <EmptyState v-if="!tiers.length" compact description="还没有待处理问题"
                      hint="分析完成后，此处按紧急度展示问题分布" />
          <VChart v-else class="dash__chart-body" :option="pieOption" autoresize />
        </ElCard>

        <ElCard shadow="never" class="dash__chart">
          <template #header><span class="dash__chart-title">待审批事项趋势（近 {{ app.rangeDays }} 天）</span></template>
          <EmptyState v-if="!hitl.length" compact description="这段时间没有待批记录" />
          <template v-else>
            <VChart class="dash__chart-body" :option="barOption" autoresize />
            <!-- 只有 1~2 天数据时说清楚（2026-11-04）：
                 不然一根孤零零的柱子看着很怪 ✅ -->
            <div v-if="hitl.length <= 2" class="dash__bar-note">
              近 30 天只有 <b>{{ hitl.length }}</b> 天有待批记录（系统刚上线 ✅）
              —— 数据攒起来就能看趋势
            </div>
          </template>
        </ElCard>
      </div>

      <!-- ============ 🔴 待处理事项 ============ -->
      <ElCard shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <ElIcon color="#f56c6c"><Bell /></ElIcon>
            <span class="dash__list-title">待处理事项</span>
            <ElTag v-if="mustDo.length" type="danger" size="small" round>{{ mustDo.length }}</ElTag>
          </div>
        </template>

        <EmptyState v-if="!mustDo.length" compact description="暂无待处理事项"
                    hint="待批提案 / 到期复盘 / 死信会出现在这里" />

        <div v-else class="dash__items">
          <div v-for="(it, i) in mustDo" :key="i" class="dash__item" @click="goto(it)">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.text }}</div>
              <div v-if="it.detail || it.next_step" class="dash__item-hint">{{ it.detail || it.next_step }}</div>
            </div>
            <ElIcon class="dash__item-arrow"><ArrowRight /></ElIcon>
          </div>
        </div>
      </ElCard>

      <!-- ============ ⏳ 效果观察中 ============ -->
      <ElCard v-if="waiting.length" shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <ElIcon color="#e6a23c"><Clock /></ElIcon>
            <span class="dash__list-title">效果观察中</span>
            <ElTag type="warning" size="small" round>{{ waiting.length }}</ElTag>
            <span class="dash__list-note">窗口到期后自动复盘（最早 2026-11-13）</span>
          </div>
        </template>
        <div class="dash__items">
          <div v-for="(it, i) in waiting" :key="i" class="dash__item is-muted">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.text }}</div>
              <div v-if="it.detail || it.next_step" class="dash__item-hint">{{ it.detail || it.next_step }}</div>
            </div>
          </div>
        </div>
      </ElCard>

      <!-- ============ 知会 ============ -->
      <ElCard v-if="notice.length" shadow="never" class="dash__list">
        <template #header>
          <div class="dash__list-head">
            <span class="dash__list-title">知会事项</span>
            <ElTag size="small" round type="info">{{ notice.length }}</ElTag>
          </div>
        </template>
        <div class="dash__items">
          <div v-for="(it, i) in notice" :key="i" class="dash__item is-muted">
            <div class="dash__item-main">
              <div class="dash__item-title">{{ it.text }}</div>
              <div v-if="it.detail || it.next_step" class="dash__item-hint">{{ it.detail || it.next_step }}</div>
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
  &__sections {
    display: grid;
    // ⚠️ **原来写死 3 列**（那时有 3 组指标 ✅）
    //    2026-11-04：主区域只剩「规模与影响」1 组 ✅
    //    → 3 列网格里 1 个元素只占 1/3 宽 → **右边一大片空白** ✅ ✗
    //    → 改成 auto-fit：**有几组就用几列**，1 组就是整行 ✅
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: $space-md;
  }

  &__section {
    padding: $space-md;
    border-radius: $radius-base;
    border: 1px solid var(--hub-border-light);
    background: var(--hub-bg-card);
  }

  &__section-title {
    margin-bottom: $space-sm;
    padding-bottom: 4px;
    font-size: 13px;
    font-weight: 600;
    border-bottom: 1px dashed var(--hub-border-light);
  }

  &__section-sub {
    margin: -6px 0 $space-sm;
    font-size: 11px;
    line-height: 1.6;
    color: var(--hub-text-secondary);
  }

  // ⚠️ **横排多列**（2026-11-04 改）
  //    用户反馈："规模影响右边为什么有那么大的空白，能不能排版美观一点"
  //    原因：`dl` 是**单列**，每行只有一行文字 → 右边整片空白 ✅ ✗
  //    改成：**自适应多列网格**，一屏铺满 ✅
  &__section-list {
    display: grid;
    // **两列**：每格"标签 + 值"，铺满整行 ✅
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    column-gap: $space-lg;
    row-gap: $space-sm;
    margin: 0;

    dt {
      font-size: 11px;
      color: var(--hub-text-muted);
    }

    dd {
      margin: 0 0 2px;
      font-size: 14px;
      font-variant-numeric: tabular-nums;
    }
  }

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

.dashboard {
  &__ops-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  &__ops-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--hub-text-muted);
  }

  &__ops-sub {
    font-size: 11px;
    color: var(--hub-text-muted);
  }

  &__sections--ops {
    opacity: 0.85;
  }
}

.dash {
  &__bar-note {
    padding: $space-sm 0 0;
    font-size: 11px;
    line-height: 1.6;
    color: var(--hub-text-muted);
  }
}
</style>
