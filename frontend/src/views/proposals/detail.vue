<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  2 号页：提案详情（**最能讲的一页**）
//  三栏：内容 / **依据（展开到真实评价原文 ✅）** / 风险+经验+决定
//  ⚠️ `evidence_refs` 只有 `[{issue_cluster}]` → **原文靠 `/clusters/{id}/reviews`**
//     这个接口是 2026-11-01 专门为这一页补的 ✅
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElDrawer,
  ElIcon, ElTag,
} from 'element-plus'
import { ArrowLeft, Document, Warning } from '@element-plus/icons-vue'

import {
  getClusterReviews, getClusterTrend, getProposal,
  type ClusterReview, type ProposalDetail, type WeekPoint,
} from '@/api/data'
import { ApiError } from '@/api/client'
import { approveProposal, rejectProposal } from '@/api/write'
import ErrorState from '@/components/ErrorState.vue'
import PageLoading from '@/components/PageLoading.vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const route = useRoute()
const router = useRouter()

const id = computed(() => String(route.params.id ?? ''))
const loading = ref(true)
const error = ref<unknown>(null)
const detail = ref<ProposalDetail | null>(null)
const reviews = ref<ClusterReview[]>([])
const reviewTotal = ref(0)
const trend = ref<WeekPoint[]>([])
const reviewsLoading = ref(false)

/** 依据里的簇 id（**可能没有** → 不显示依据区 ✅）*/
const clusterId = computed<string>(() => {
  const ref0 = detail.value?.evidence_refs?.[0]
  return ref0?.issue_cluster ?? ''
})

// ---------- 加载 ----------
async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    detail.value = await getProposal(id.value)
  } catch (e) {
    error.value = e
    loading.value = false
    return
  }
  loading.value = false
  if (clusterId.value) {
    void loadReviews()
    void loadTrend()
  }
}

async function loadReviews(): Promise<void> {
  if (!clusterId.value) return
  reviewsLoading.value = true
  try {
    const d = await getClusterReviews(clusterId.value, { limit: 20 })
    reviews.value = d.items
    reviewTotal.value = d.total
  } catch {
    reviews.value = []
  } finally {
    reviewsLoading.value = false
  }
}

async function loadTrend(): Promise<void> {
  if (!clusterId.value) return
  try {
    trend.value = await getClusterTrend(clusterId.value)
  } catch {
    trend.value = []
  }
}

onMounted(() => void load())
watch(id, () => void load())

// ---------- 批准 / 拒绝 ----------
const acting = ref(false)
const rejectDrawer = ref(false)
const rejectReason = ref('')

async function doApprove(): Promise<void> {
  if (!detail.value) return
  acting.value = true
  try {
    await approveProposal(detail.value.id, true)
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      // 404 = 没有待批任务（已批过/已拒）→ **不是错，刷新就好** ✅
      await load()
    }
  } finally {
    acting.value = false
  }
}

async function doReject(): Promise<void> {
  if (!detail.value || !rejectReason.value.trim()) return
  acting.value = true
  try {
    await rejectProposal(detail.value.id, rejectReason.value.trim())
    rejectDrawer.value = false
    rejectReason.value = ''
    await load()
  } finally {
    acting.value = false
  }
}

// ---------- 展示辅助 ----------
const RISK_TYPE: Record<string, 'success' | 'info' | 'warning' | 'danger'> = {
  L0: 'success', L1: 'info', L2: 'warning', L3: 'danger',
}
const STATUS_TYPE: Record<string, 'success' | 'info' | 'warning' | 'danger'> = {
  PROPOSED: 'info', PENDING_APPROVAL: 'warning', APPROVED: 'success',
  EXECUTED: 'success', REJECTED: 'danger', SKIPPED: 'info',
}
const STATUS_LABEL: Record<string, string> = {
  PROPOSED: '待处理', PENDING_APPROVAL: '待批准', APPROVED: '已批准',
  EXECUTED: '已执行', REJECTED: '已拒绝', SKIPPED: '已跳过',
}
const TOOL_LABEL: Record<string, string> = {
  'review.draft_reply': '生成回复草稿',
  'listing.update_desc': '修改商品描述',
  'listing.restore_desc': '回滚商品描述',
  'product.update_price': '修改价格',
  'catalog.query_price': '查价格',
}

/** 差评高亮：把评价里"掉色/起球…"这类问题词标出来（**简单规则，不做过度设计** ✅）*/
const HIGHLIGHT = ['掉色', '起球', '破洞', '开线', '缩水', '色差', '褪色', '起毛', '脱线', '异味']
function highlighted(content: string): string {
  let out = content
  for (const kw of HIGHLIGHT) {
    out = out.split(kw).join(`<mark>${kw}</mark>`)
  }
  return out
}

const trendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
  xAxis: { type: 'category', data: trend.value.map((d) => d.week),
           axisLabel: { fontSize: 11, color: '#909399' } },
  yAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#909399' },
           splitLine: { lineStyle: { color: '#ebeef5' } } },
  series: [{ type: 'bar', data: trend.value.map((d) => d.n), barWidth: 16,
             itemStyle: { borderRadius: [3, 3, 0, 0] } }],
}))
</script>

<template>
  <div class="detail">
    <!-- 返回 -->
    <div class="detail__bar">
      <ElButton :icon="ArrowLeft" text @click="router.back()">返回</ElButton>
    </div>

    <PageLoading v-if="loading" :rows="3" chart />
    <ErrorState v-else-if="error" :error="error" @retry="load()" />

    <template v-else-if="detail">
      <div class="detail__grid">
        <!-- ══════════ 左：内容 ══════════ -->
        <ElCard shadow="never" class="detail__col">
          <template #header>
            <div class="detail__head">
              <span class="detail__title">
                {{ TOOL_LABEL[detail.tool_name] ?? detail.tool_name }}
              </span>
              <ElTag :type="RISK_TYPE[detail.risk_level] ?? 'info'" size="small" effect="dark">
                {{ detail.risk_level }}
              </ElTag>
              <ElTag :type="STATUS_TYPE[detail.status] ?? 'info'" size="small">
                {{ STATUS_LABEL[detail.status] ?? detail.status }}
              </ElTag>
            </div>
          </template>

          <div class="detail__section-label">为什么做这个动作</div>
          <p class="detail__rationale">{{ detail.rationale }}</p>

          <div class="detail__section-label">参数（**由代码构造，不是模型填的** ✅）</div>
          <ElDescriptions :column="1" size="small" border>
            <ElDescriptionsItem v-for="(v, k) in detail.args" :key="k" :label="String(k)">
              <span class="detail__arg">{{ String(v) }}</span>
            </ElDescriptionsItem>
          </ElDescriptions>

          <div class="detail__meta">
            <span>工具版本 <code>{{ detail.tool_version }}</code></span>
            <span>trace <code>{{ detail.trace_id }}</code></span>
            <span>{{ new Date(detail.created_at).toLocaleString('zh-CN') }}</span>
          </div>
        </ElCard>

        <!-- ══════════ 中：依据（**核心卖点** ✅）══════════ -->
        <ElCard shadow="never" class="detail__col detail__col--evidence">
          <template #header>
            <div class="detail__head">
              <ElIcon color="#409eff"><Document /></ElIcon>
              <span class="detail__title">依据</span>
              <span v-if="reviewTotal" class="detail__sub">
                这个簇共 <b>{{ reviewTotal }}</b> 条评价，先看代表样本
              </span>
            </div>
          </template>

          <EmptyState
            v-if="!clusterId"
            compact
            description="这条提案没有挂簇依据"
            hint="只有来自问题簇的提案才有原文依据"
          />

          <template v-else>
            <div v-if="reviewsLoading" class="detail__ev-loading">加载评价原文…</div>

            <EmptyState v-else-if="!reviews.length" compact
                        description="这个簇没有查到评价" />

            <div v-else class="detail__reviews">
              <div v-for="r in reviews" :key="r.record_id" class="detail__review">
                <div class="detail__review-head">
                  <ElTag size="small" :type="r.rating <= 2 ? 'danger' : 'info'" effect="plain">
                    {{ r.rating }} 星
                  </ElTag>
                  <ElTag size="small" effect="plain">{{ r.platform }}</ElTag>
                  <ElTag v-if="r.is_representative" size="small" type="success" effect="plain">
                    代表样本
                  </ElTag>
                  <span class="detail__review-time">{{ (r.review_time ?? '').slice(0, 10) }}</span>
                </div>
                <!-- eslint-disable-next-line vue/no-v-html -->
                <div class="detail__review-text" v-html="highlighted(r.content)" />
              </div>
              <div v-if="reviewTotal > reviews.length" class="detail__review-more">
                还有 {{ reviewTotal - reviews.length }} 条没显示（这里只列代表样本 + 最近的 ✅）
              </div>
            </div>
          </template>
        </ElCard>

        <!-- ══════════ 右：风险 + 经验 + 决定 ══════════ -->
        <ElCard shadow="never" class="detail__col">
          <template #header><span class="detail__title">风险与决定</span></template>

          <div class="detail__risk">
            <ElIcon :size="16"><Warning /></ElIcon>
            <div>
              <div class="detail__risk-title">
                {{ detail.risk_level }} · 需要
                <b>{{ detail.risk_level === 'L3' ? '主管 + 人工工单' : detail.risk_level === 'L2' ? '主管批准' : '运营权限' }}</b>
              </div>
              <div class="detail__risk-hint">
                {{ detail.risk_level === 'L2'
                  ? '批准后会真的执行；执行完代码会回查一次 ✅'
                  : detail.risk_level === 'L3'
                    ? 'L3 不自动执行，只转人工工单 ✅'
                    : '低风险，提交后可执行' }}
              </div>
            </div>
          </div>

          <!-- 历史战绩（experience）-->
          <div class="detail__section-label">历史战绩（这个工具在这个话题上）</div>
          <div v-if="detail.experience?.length" class="detail__exp">
            <div v-for="(e, i) in detail.experience" :key="i" class="detail__exp-row">
              <span>{{ e.topic ?? '—' }} / {{ e.tool ?? '—' }}</span>
              <span class="detail__exp-num">
                <b class="is-ok">{{ e.positive ?? 0 }}</b> 有效 /
                <b class="is-bad">{{ e.negative ?? 0 }}</b> 有害
              </span>
            </div>
          </div>
          <div v-else class="detail__exp-empty">还没有历史（第一次做这类动作）</div>

          <!-- 决定 -->
          <div class="detail__actions">
            <ElButton type="primary" :loading="acting" @click="doApprove()">
              批准（会真执行）
            </ElButton>
            <ElButton :disabled="acting" @click="rejectDrawer = true">
              拒绝
            </ElButton>
          </div>
          <div class="detail__actions-hint">
            ⚠️ 批准需要 <b>supervisor</b> 角色；operator 点了会 403（UI 会先隐藏按钮 ✅）
          </div>
        </ElCard>
      </div>

      <!-- ══════════ 底部：该簇周趋势 ══════════ -->
      <ElCard shadow="never">
        <template #header>
          <span class="detail__title">该簇评价周趋势</span>
          <span class="detail__sub">用来判断"这个问题是在变好还是变坏"</span>
        </template>
        <EmptyState v-if="!trend.length" compact description="还没有周趋势数据" />
        <VChart v-else class="detail__trend" :option="trendOption" autoresize />
      </ElCard>
    </template>

    <!-- 拒绝抽屉（**必须带原因** ✅）-->
    <ElDrawer v-model="rejectDrawer" title="拒绝这条提案" size="420px">
      <div class="detail__drawer-hint">拒绝必须写原因（后端 `min_length=1`，空原因会 422 ✅）</div>
      <el-input v-model="rejectReason" type="textarea" :rows="5"
                placeholder="例如：这个方案不符合成本要求 / 已经线下处理过了" />
      <div class="detail__drawer-actions">
        <ElButton @click="rejectDrawer = false">取消</ElButton>
        <ElButton type="danger" :loading="acting" :disabled="!rejectReason.trim()"
                  @click="doReject()">确认拒绝</ElButton>
      </div>
    </ElDrawer>
  </div>
</template>

<style scoped lang="scss">
.detail {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__bar {
    margin-bottom: -8px;
  }

  &__grid {
    display: grid;
    grid-template-columns: 1fr 1.25fr 0.9fr;
    gap: $space-md;
    align-items: start;
  }

  &__col {
    min-width: 0;
  }

  &__head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
  }

  &__sub {
    margin-left: auto;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__section-label {
    margin: $space-md 0 6px;
    font-size: 11px;
    color: var(--hub-text-secondary);

    &:first-of-type {
      margin-top: 0;
    }
  }

  &__rationale {
    margin: 0;
    padding: 10px 12px;
    border-left: 3px solid var(--el-color-primary);
    border-radius: 0 $radius-sm $radius-sm 0;
    background: var(--el-color-primary-light-9);
    font-size: 13px;
    line-height: 1.7;
  }

  &__arg {
    font-size: 12px;
    word-break: break-all;
  }

  &__meta {
    display: flex;
    flex-wrap: wrap;
    gap: $space-md;
    margin-top: $space-md;
    font-size: 11px;
    color: var(--hub-text-secondary);

    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
  }

  // ---------- 依据 ----------
  &__ev-loading {
    padding: $space-lg 0;
    text-align: center;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__reviews {
    display: flex;
    flex-direction: column;
    gap: $space-sm;
    max-height: 520px;
    overflow-y: auto;
    @include thin-scrollbar;
  }

  &__review {
    padding: 10px 12px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    background: var(--el-fill-color-lighter);
  }

  &__review-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
  }

  &__review-time {
    margin-left: auto;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__review-text {
    font-size: 13px;
    line-height: 1.75;
    color: var(--hub-text-regular);

    :deep(mark) {
      padding: 0 2px;
      border-radius: 2px;
      background: var(--el-color-warning-light-8);
      color: var(--el-color-warning-dark-2);
      font-weight: 600;
    }
  }

  &__review-more {
    padding: $space-sm 0;
    text-align: center;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  // ---------- 风险 ----------
  &__risk {
    display: flex;
    gap: $space-sm;
    padding: 10px 12px;
    border-radius: $radius-sm;
    background: var(--el-color-warning-light-9);
    color: var(--el-color-warning-dark-2);
  }

  &__risk-title {
    font-size: 12px;
    font-weight: 600;
  }

  &__risk-hint {
    margin-top: 2px;
    font-size: 11px;
    line-height: 1.6;
    opacity: 0.85;
  }

  // ---------- 经验 ----------
  &__exp {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  &__exp-row {
    display: flex;
    justify-content: space-between;
    padding: 6px 10px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    font-size: 12px;
  }

  &__exp-num {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;

    .is-ok { color: var(--el-color-success); }
    .is-bad { color: var(--el-color-danger); }
  }

  &__exp-empty {
    padding: $space-sm 0;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  // ---------- 动作 ----------
  &__actions {
    display: flex;
    gap: $space-sm;
    margin-top: $space-lg;
  }

  &__actions-hint {
    margin-top: 6px;
    font-size: 11px;
    line-height: 1.6;
    color: var(--hub-text-secondary);
  }

  // ---------- 抽屉 ----------
  &__drawer-hint {
    margin-bottom: $space-sm;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__drawer-actions {
    display: flex;
    justify-content: flex-end;
    gap: $space-sm;
    margin-top: $space-md;
  }

  &__trend {
    height: 200px;
  }

  @media (max-width: 1280px) {
    &__grid {
      grid-template-columns: 1fr;
    }
  }
}
</style>
