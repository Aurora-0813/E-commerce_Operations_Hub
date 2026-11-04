<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  3 号页：问题全景（图表最多的一页）
//  数据源（**全部真实接口**）：
//    GET /api/charts/topic-distribution   问题类型饼
//    GET /api/charts/clusters             簇条数（条形 + 表格同源）
//    GET /api/clusters/{id}/factors       四因子（**只给真实输入 + note**）
//    GET /api/charts/price-vs-badrate     价格 vs 差评率散点
//    GET /api/charts/gmv-top              GMV TopN
//    GET /api/charts/bad-rate-trend       差评率趋势（**现在可能是空**）
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElCard, ElTable, ElTableColumn, ElTag, ElSelect, ElOption } from 'element-plus'
import { Download, Refresh } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
// ⚠️ **`LineChart` 也要注册**（2026-11-02 修）：我给趋势图加了 line series ✅
import { BarChart, LineChart, PieChart, RadarChart, ScatterChart } from 'echarts/charts'
// ⚠️ **`TitleComponent` 也要注册**（2026-11-02 修）：我给雷达图加了 title（标簇名 ✅）
import {
  GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent,
} from 'echarts/components'

import {
  getBadRateTrend, getClusters, getFactors, getGmvTop, getPriceVsBadrate,
  getTopicDistribution,
  type ClusterRow, type FactorsResp, type GmvItem, type PricePoint,
  type TierItem, type TopicItem, type TrendPoint,
} from '@/api/data'
import { getClusterTiers } from '@/api/data'
import { downloadExport } from '@/api/write'
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/EmptyState.vue'
import http from '@/api/client'
import { TOPIC } from '@/config/labels'
import ErrorState from '@/components/ErrorState.vue'
import PageLoading from '@/components/PageLoading.vue'
import { useAppStore } from '@/stores/app'

use([CanvasRenderer, PieChart, BarChart, LineChart, RadarChart, ScatterChart,
     GridComponent, TooltipComponent, LegendComponent, RadarComponent, TitleComponent])

const router = useRouter()
const app = useAppStore()

const loading = ref(true)
const error = ref<unknown>(null)

const tiers = ref<TierItem[]>([])
const topics = ref<TopicItem[]>([])
const clusters = ref<ClusterRow[]>([])
const gmv = ref<GmvItem[]>([])
const prices = ref<PricePoint[]>([])
const trend = ref<TrendPoint[]>([])
const factors = ref<FactorsResp | null>(null)
/** 选中簇（四因子跟着它变）*/
const selected = ref<string>('')
/** 簇表排序 */
const sort = ref<'reviews' | 'priority' | 'label'>('reviews')

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  const [t, tp, cl, g, p, tr] = await Promise.allSettled([
    getClusterTiers(), getTopicDistribution(), getClusters(sort.value),
    getGmvTop(10), getPriceVsBadrate(), getBadRateTrend(app.rangeDays),
  ])
  if (t.status === 'fulfilled') tiers.value = t.value
  if (tp.status === 'fulfilled') topics.value = tp.value
  if (cl.status === 'fulfilled') {
    clusters.value = cl.value
    // 默认选中条数最多的簇（四因子雷达要有东西可看 ✅）
    if (!selected.value && cl.value.length) selected.value = cl.value[0].id
  } else {
    error.value = cl.reason
  }
  if (g.status === 'fulfilled') gmv.value = g.value
  if (p.status === 'fulfilled') prices.value = p.value
  if (tr.status === 'fulfilled') trend.value = tr.value
  loading.value = false
  if (selected.value) void loadFactors(selected.value)
}

/**
 * 拉四因子
 * ⚠️ **2026-11-02 修**：用户反馈「换了簇，四因子图每个都一样」✅
 *    原因有两个可能：请求没重发 重发了但图表没重绘
 *    修法：**先清空（让图消失）→ 再拉 → 图上标出当前簇名** ✅
 *         这样"切了没生效"一眼能看出来 ✅
 */
const factorsLoading = ref(false)

/** 数据里最新的日期（给空态提示用 ✅）*/
const latestDate = computed<string>(() => {
  const ds = trend.value.map((d) => d.date).filter(Boolean) as string[]
  if (!ds.length) return ''
  return ds.reduce((a, b) => (a > b ? a : b), '')
})
async function loadFactors(cid: string): Promise<void> {
  factorsLoading.value = true
  factors.value = null          // **先清空**（否则看起来"没变" ✅）
  try {
    factors.value = await getFactors(cid)
  } catch {
    factors.value = null
  } finally {
    factorsLoading.value = false
  }
}

onMounted(() => {
  void load()
  void loadProposalClusters()
})
watch(() => app.range, () => void load())
watch(selected, (v) => { if (v) void loadFactors(v) })
watch(sort, () => void load())

// ---------- 图问题类型饼 ----------
const pieTopic = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
  legend: { bottom: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11 } },
  series: [{
    type: 'pie', radius: ['48%', '72%'], center: ['50%', '42%'],
    label: { show: false },
    data: topics.value.map((x) => ({
      // **用共享字典**（含 desc_mismatch→描述不符 / misship→发错货 ✅）
        name: TOPIC[x.topic]?.text ?? x.topic, value: x.reviews,
    })),
  }],
}))

// ---------- 图簇条数（横向条形 top 12）----------
const barClusters = computed(() => {
  const top = [...clusters.value].sort((a, b) => b.reviews - a.reviews).slice(0, 12).reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#ebeef5' } },
             axisLabel: { fontSize: 11, color: '#909399' } },
    yAxis: { type: 'category', data: top.map((c) => c.label),
             axisLabel: { fontSize: 11 }, axisTick: { show: false } },
    series: [{
      type: 'bar', data: top.map((c) => c.reviews), barWidth: 12,
      itemStyle: { borderRadius: [0, 3, 3, 0] },
    }],
  }
})

// ---------- 图四因子雷达（**只画真实输入，不编归一化** ✅）----------
// ⚠️ **刻度必须用「全局最大值」**（2026-11-03 修 —— 这是「每个簇都一样」的真根因）
//    原来：`max: Math.max(i.review_count * 1.2, 10)` ← 按**自己**的值算 ✅
//      → 192/(192*1.2) = 43/(43*1.2) = **0.833**，每个簇画出来都是同一个形状 ✅ ✗
//    改成：`max` 取**所有簇的最大值** → 192 的簇画到顶，43 的簇只画到 1/4 ✅
const gmax = (pick: (c: ClusterRow) => number, floor = 1): number =>
  Math.max(...clusters.value.map(pick), floor)

const radar = computed(() => {
  const f = factors.value
  if (!f) return null
  const i = f.inputs
  // ⚠️ 这里**不归一化**：直接把真实数值放进雷达（量纲不同也会显示，但**不造假** ✅）
  //    后端 `note` 里明确写了"未归一化：这些是库里的原始输入" ✅
  return {
    // **标题带上簇名**（用户说「每个都一样」→ 现在能看出是哪个簇 ✅）
    title: { text: f.label, left: 'center', top: 0,
             textStyle: { fontSize: 12, color: '#606266' } },
    tooltip: { trigger: 'item' },
    radar: {
      indicator: [
        // **全局最大值**（不是自己 ×1.2 ✅）
        { name: '评价条数', max: gmax((c) => c.reviews, 10) },
        { name: '严重度', max: 1 },
        // 优先级分是 0~100 的量纲（`hub/analysis/priority.py` ✅）→ 固定 100
        { name: '优先级分', max: 100 },
        { name: '涉及商品', max: gmax((c) => c.spus, 3) },
      ],
      radius: '58%',
      center: ['50%', '58%'],
      axisLabel: { fontSize: 10, color: '#909399' },
      splitLine: { lineStyle: { color: '#ebeef5' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [
          i.review_count,
          i.severity ?? 0,
          i.priority_score ?? 0,
          clusters.value.find((c) => c.id === f.cluster_id)?.spus ?? 0,
        ],
        name: f.label,
        areaStyle: { opacity: 0.18 },
      }],
    }],
  }
})

/**
 * 四因子的**真实数值**（2026-11-04 加）
 *
 * 用户反馈："为什么我看着还是没区别"
 *   → 雷达只有形状，看不到具体数字 ✅
 *   → 这里把 4 个真实值列出来，**一眼能对比** ✅
 * （雷达的刻度是"全体簇的最大值"归一化 ✅，所以小簇的图形确实会小 ✅）
 */
const radarValues = computed(() => {
  const f = factors.value
  if (!f) return []
  const spus = clusters.value.find((c) => c.id === f.cluster_id)?.spus ?? 0
  return [
    { label: '评价条数', value: `${f.inputs.review_count ?? 0} 条` },
    { label: '严重度', value: (f.inputs.severity ?? 0).toFixed(2) },
    { label: '优先级分', value: (f.inputs.priority_score ?? 0).toFixed(1) },
    { label: '涉及商品', value: `${spus} 个` },
  ]
})

// ---------- 图价格散点 ----------
const scatter = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (p: { data: [number, number, string, number] }) =>
      `${p.data[2]}<br/>价格 ¥${p.data[0]}<br/>差评率 ${(p.data[1] * 100).toFixed(1)}%`,
  },
  // ⚠️ **2026-11-02 修**：`left: 8` 太窄 → Y 轴名字被裁 ✅
  //    而且坐标轴名要**留出空间**（`nameGap` + `nameLocation` ✅）
  grid: { left: 56, right: 28, top: 32, bottom: 32, containLabel: true },
  xAxis: { type: 'value', name: '价格（元）', nameLocation: 'middle', nameGap: 26,
           nameTextStyle: { fontSize: 11, color: '#909399' },
           axisLabel: { fontSize: 11, color: '#909399' },
           splitLine: { lineStyle: { color: '#ebeef5' } } },
  yAxis: { type: 'value', name: '差评率', nameLocation: 'middle', nameGap: 42,
           nameTextStyle: { fontSize: 11, color: '#909399' },
           axisLabel: { fontSize: 11, color: '#909399', formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
           splitLine: { lineStyle: { color: '#ebeef5' } } },
  series: [{
    type: 'scatter',
    // ⚠️ **2026-11-02 修**：原来 `Math.sqrt(count)*2` 最大 28px → 用户说「散点太大」✅
    //    改成 **8~14px**（有区分度但不糊成一片 ✅）
    // ⚠️ **固定 7px**（2026-11-03 再修：8~14px 用户还说「太大」✅）
    //    气泡大小表达「评价数」的意图保留在 tooltip 里 ✅
    symbolSize: 7,
    data: prices.value.map((x) => [x.price, x.review_rate, x.title, x.review_count]),
    itemStyle: { opacity: 0.7 },
  }],
}))

// ---------- 图差评率趋势 ----------
const lineTrend = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
  xAxis: { type: 'category', data: trend.value.map((d) => (d.date ?? '').slice(5)),
           axisLabel: { fontSize: 11, color: '#909399' } },
  yAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#909399',
           formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
           splitLine: { lineStyle: { color: '#ebeef5' } } },
  series: [{
    type: 'line', smooth: true, showSymbol: false,
    data: trend.value.map((d) => d.bad_rate ?? 0),
    name: '差评率',
    areaStyle: { opacity: 0.12 },
  }],
}))

// ---------- 动作 ----------
function openCluster(row: ClusterRow): void {
  selected.value = row.id
  // **按簇跳转**（2026-11-04 修）：原来按 topic 筛 → 点「开线易破」会带出所有质量问题 ✅ ✗
  void router.push({ path: '/proposals', query: { cluster_id: row.id } })
}
/**
 * 导出 xlsx
 *
 * ⚠️ **不能用 `window.open('/api/export/clusters')`**（2026-11-01 修的真 bug）
 *    `window.open` 是**新开一个标签页发 GET** ✅ → **不会带 `Authorization` 头** ✅
 *    → 后端直接 **401** → 用户看到空白页 ✅ ✗
 * 正确做法：**axios 带 token 拿 blob → 前端造一个下载链接** ✅
 *    （`downloadExport()` 已经在 `api/write.ts` 里实现了 ✅）
 */
async function exportXlsx(): Promise<void> {
  try {
    const { blob, rows } = await downloadExport('clusters')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'clusters.xlsx'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${rows} 行`)
  } catch {
    // 401/403/500 都由 axios 拦截器提示过 ✅
  }
}

// ---------- 表格 ----------
const TIER_TYPE: Record<string, 'danger' | 'warning' | 'primary' | 'info'> = {
  P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info',
}
function tierType(t: string): 'danger' | 'warning' | 'primary' | 'info' {
  return TIER_TYPE[t] ?? 'info'
}

// **哪些簇已经有提案**（2026-11-04 加）
//   只有 P0/P1 才生成提案 ✅ -> 簇明细里要标明，否则用户以为漏了
const proposalClusterIds = ref<Set<string>>(new Set())
const hasProposal = (cid: string): boolean => proposalClusterIds.value.has(cid)

async function loadProposalClusters(): Promise<void> {
  try {
    const { data } = await http.get<{ items: Array<{ cluster_id?: string }> }>(
      // ⚠️ limit 上限是 **200**（后端 `le=200`）→ 写 500 会 422 ✅ ✗
      '/api/proposals', { params: { limit: 200 } })
    proposalClusterIds.value = new Set(
      (data.items ?? []).map((x) => String(x.cluster_id ?? '')).filter(Boolean))
  } catch {
    proposalClusterIds.value = new Set()
  }
}

</script>

<template>
  <div class="problems">
    <div class="problems__head">
      <div>
        <h2 class="problems__title">问题全景</h2>
        <div class="problems__sub">
          22 个问题簇 · 数据全部来自真实表
        </div>
      </div>
      <div class="problems__actions">
        <ElButton :icon="Refresh" :loading="loading" @click="load">刷新</ElButton>
        <ElButton :icon="Download" @click="exportXlsx">导出 xlsx</ElButton>
      </div>
    </div>

    <PageLoading v-if="loading && !clusters.length" :rows="3" chart />
    <ErrorState v-else-if="error" :error="error" @retry="load" />

    <template v-else>
      <!-- 第一排：问题类型饼 + 簇条数条形 -->
      <div class="problems__row">
        <ElCard shadow="never">
          <template #header><span class="problems__card-title">问题类型分布</span></template>
          <EmptyState v-if="!topics.length" compact description="还没有分类结果"
                      hint="先跑分类" command="hub topic" />
          <VChart v-else class="problems__chart" :option="pieTopic" autoresize />
        </ElCard>

        <ElCard shadow="never">
          <template #header><span class="problems__card-title">簇条数 Top 12</span></template>
          <EmptyState v-if="!clusters.length" compact description="还没有问题簇"
                      hint="先跑聚类" command="hub cluster" />
          <VChart v-else class="problems__chart" :option="barClusters" autoresize />
        </ElCard>
      </div>


      <!-- 第三排：价格散点 + 差评率趋势 -->
      <div class="problems__row">
        <ElCard shadow="never">
          <template #header><span class="problems__card-title">价格 vs 差评率</span></template>
          <EmptyState v-if="!prices.length" compact description="还没有价格背离数据"
                      hint="先跑价格检测" command="hub price" />
          <VChart v-else class="problems__chart" :option="scatter" autoresize />
        </ElCard>

      <!-- 第二排：四因子雷达（跟选中簇联动）-->
      <ElCard shadow="never">
        <template #header>
          <div class="problems__card-head">
            <span class="problems__card-title">簇四因子</span>
            <ElSelect v-model="selected" size="small" class="problems__select" placeholder="选一个簇">
              <ElOption v-for="c in clusters" :key="c.id" :value="c.id"
                        :label="`${c.label}（${c.tier} · ${c.reviews} 条）`" />
            </ElSelect>
            <span v-if="factors" class="problems__note">{{ factors.note }}</span>
          </div>
        </template>
        <div v-if="factorsLoading" class="problems__muted">正在加载四因子…</div>
        <EmptyState v-else-if="!radar" compact description="选一个簇看看它的四因子" />
        <template v-else>
          <VChart class="problems__chart problems__chart--sm" :option="radar" autoresize />
          <!-- 把 4 个真实值列出来（形状之外，看数字 ✅）-->
          <div class="problems__radar-vals">
            <div v-for="v in radarValues" :key="v.label" class="problems__radar-val">
              <span class="problems__muted">{{ v.label }}</span>
              <b>{{ v.value }}</b>
            </div>
          </div>
        </template>
      </ElCard>

      </div>

      <!-- 第四排：差评率趋势 + GMV Top10（同一行 ✅ 2026-11-04）-->
      <div class="problems__row">
          <ElCard shadow="never">
            <template #header>
              <span class="problems__card-title">差评率趋势（近 {{ app.rangeDays }} 天）</span>
            </template>
            <!-- ⚠️ 这个接口现在可能是空数组 → 走空态，不画空图 -->
            <!-- ⚠️ 2026-11-02 修：把「为什么空」说清楚 -->
            <!--    用户反馈「差评率趋势没有图」→ 实测是时间范围选错了 -->
            <!--    数据最新到 2026-10-21，而默认「近 7 天」= 10-26~11-02 → 一条都没有 -->
            <EmptyState v-if="!trend.length" compact
                        :description="`近 ${app.rangeDays} 天没有差评率数据`"
                        :hint="`数据的最新日期是 ${latestDate || '未知'}。可以切换成「近 30 天」再看，或者先导入新数据`" />
            <VChart v-else class="problems__chart" :option="lineTrend" autoresize />
          </ElCard>

        <ElCard shadow="never">
          <template #header><span class="problems__card-title">GMV Top 10（按商品）</span></template>
          <EmptyState v-if="!gmv.length" compact description="还没有销售数据"
                        hint="来自「销售」文件的日粒度数据，不依赖分析结果" />
          <ElTable v-else :data="gmv" size="small" stripe>
            <ElTableColumn type="index" label="#" width="52" />
            <ElTableColumn prop="title" label="商品" min-width="220" show-overflow-tooltip />
            <ElTableColumn prop="units" label="销量" width="100" align="right" class-name="is-numeric" />
            <ElTableColumn label="GMV" width="140" align="right" class-name="is-numeric">
              <template #default="{ row }">
                ¥{{ (row.gmv / 10000).toFixed(1) }} 万
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </div>

      <!-- 第五排：簇明细表 -->
      <ElCard shadow="never">
        <template #header>
          <div class="problems__card-head">
            <span class="problems__card-title">簇明细（点行看提案）</span>
            <ElSelect v-model="sort" size="small" class="problems__select" style="width: 130px">
              <ElOption value="reviews" label="按条数" />
              <ElOption value="priority" label="按优先级" />
              <ElOption value="label" label="按名称" />
            </ElSelect>
          </div>
        </template>
        <ElTable :data="clusters" size="small" stripe @row-click="openCluster">
          <ElTableColumn prop="label" label="簇" min-width="180" show-overflow-tooltip />
          <ElTableColumn label="P 档" width="80">
            <template #default="{ row }">
              <ElTag :type="tierType(row.tier)" size="small" effect="dark">{{ row.tier }}</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="reviews" label="评价数" width="100" align="right" class-name="is-numeric" />
          <ElTableColumn prop="spus" label="商品数" width="90" align="right" class-name="is-numeric" />
          <!-- 有没有提案（2026-11-04 加）—— 只有 P0/P1 才生成 ✅ -->
          <ElTableColumn label="提案" width="110">
            <template #default="{ row }">
              <ElTag v-if="hasProposal(row.id)" size="small" type="success" effect="plain">
                已生成
              </ElTag>
              <span v-else class="problems__muted">
                {{ row.tier === 'P0' || row.tier === 'P1' ? '生成中…' : '暂不处理' }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <!-- 明确的按钮（2026-11-04）：行点击在 ElTable 里实测没反应 ✅ ✗ -->
              <ElButton link type="primary" size="small"
                        @click.stop="openCluster(row as ClusterRow)">
                看提案
              </ElButton>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="domain" label="品类" width="100" />
          <ElTableColumn label="优先级分" width="110" align="right" class-name="is-numeric">
            <template #default="{ row }">
              {{ row.score != null ? row.score.toFixed(3) : '—' }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>
    </template>
  </div>
</template>

<style scoped lang="scss">
.problems {
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

  &__row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: $space-md;
  }

  &__card-head {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__card-title {
    font-size: 13px;
    font-weight: 600;
  }

  &__select {
    width: 260px;
  }

  &__note {
    margin-left: auto;
    font-size: 11px;
    color: var(--hub-text-secondary);
    @include ellipsis;
    max-width: 340px;
  }

  &__chart {
    height: 300px;

    &--sm {
      height: 220px;
    }
  }

  // ⚠️ **2026-11-02 修**：两图并排时 1fr 太窄 → 1400px 以下改单列 ✅
  @media (max-width: 1400px) {
    &__row {
      grid-template-columns: 1fr;
    }
  }
}
</style>
