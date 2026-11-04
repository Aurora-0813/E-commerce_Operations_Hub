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
  ElButton, ElCard, ElCollapse, ElCollapseItem, ElDescriptions, ElDescriptionsItem,
  ElDrawer, ElIcon, ElTag, ElTooltip,
} from 'element-plus'
import { ArrowLeft, Document, Warning } from '@element-plus/icons-vue'

import {
  getClusterReviews, getClusterTrend, getProposal,
  type ClusterReview, type ProposalDetail, type WeekPoint,
} from '@/api/data'
import http, { ApiError } from '@/api/client'
import { useUserStore } from '@/stores/user'
import { approveProposal, rejectProposal } from '@/api/write'
import ErrorState from '@/components/ErrorState.vue'
import PageLoading from '@/components/PageLoading.vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent } from 'echarts/components'

use([CanvasRenderer, BarChart, GridComponent, TitleComponent, TooltipComponent])

const route = useRoute()
const router = useRouter()
const user = useUserStore()

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
/** 能不能批准：**必须后端说有待办任务** + 角色够 ✅ */
const canApprove = computed(() =>
  detail.value?.has_pending_task === true && user.hasRole('supervisor'))
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
  // **新加的 3 个工具**（2026-11-04）：不加的话标题会露出 cs.batch_task ✅ ✗
  'cs.batch_task': '生成客服批量任务',
  'factory.feedback': '反馈工厂',
  'hitl.create': '转人工',
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

/** 合规检查命中（后端 args 里带 `compliance_flags` ✅）*/
const complianceHits = computed<string[]>(() => {
  const f = detail.value?.args?.compliance_flags
  return Array.isArray(f) ? (f as string[]) : []
})

/**
 * 人话字段：**只暴露业务能看懂的 key**（2026-11-02 加）
 * 其他字段（比如内部的 `review_ids`）一律进"技术细节" ✅
 */
const FRIENDLY: Record<string, string> = {
  spu_id: '商品 id',
  platform: '平台',
  cluster_id: '问题簇',
  topic: '问题类型',
  title: '商品',
  content: '内容',
  description: '描述',
  price: '价格',
  cluster: '问题簇',
}
const friendlyArgs = computed<Record<string, unknown>>(() => {
  const a = detail.value?.args ?? {}
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(a)) {
    if (k === 'compliance_flags') continue
    const label = FRIENDLY[k]
    if (label) out[label] = v
    // 不认识的 key **不显示**（进技术细节 ✅）
  }
  return out
})

const trendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
  xAxis: { type: 'category', data: trend.value.map((d) => d.week),
           axisLabel: { fontSize: 11, color: '#909399' } },
  yAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#909399' },
           splitLine: { lineStyle: { color: '#ebeef5' } } },
  // ⚠️ **`d.reviews`**（不是 `d.n` ✅ 2026-11-02 修）
  series: [{ type: 'bar', data: trend.value.map((d) => d.reviews), barWidth: 16,
             itemStyle: { borderRadius: [3, 3, 0, 0] } }],
}))

// ══════════════════════════════════════════════════════════════
//  「这条建议凭什么？」（2026-11-04 加）
// ══════════════════════════════════════════════════════════════
// 背景：用户问「智能处置这一项有什么业务价值」→ 它和提案生成器重复
//   但它有一样 proposer 没有的东西：证据链 E1/E2/E3 + 推理轨迹
//   → 所以把它降级成「解释器」，挂在这里：proposer 说做什么，它说凭什么
const explainOpen = ref(false)
const explainLoading = ref(false)
const explainError = ref('')
const explainResult = ref<{
  result?: { recommendation?: string; confidence?: number;
             tool?: string; needs_human?: boolean }
  steps?: Array<{ name?: string; ms?: number; ok?: boolean }>
  evidence?: Array<{ ref?: string; source?: string; text?: string }>
} | null>(null)

/** 打开解释面板：提交 agent 任务（同簇幂等，409 就用已有的）*/
async function openExplain(): Promise<void> {
  const cid = String((detail.value?.args as { cluster_id?: string } | undefined)?.cluster_id ?? '')
  explainOpen.value = true
  explainError.value = ''
  explainResult.value = null
  if (!cid) {
    explainError.value = '这条提案没关联问题簇（比如调价关心的是商品），没法展开推理'
    return
  }
  explainLoading.value = true
  try {
    let rid = ''
    try {
      const { data } = await http.post<{ run_id?: string }>('/api/handle', { cluster_id: cid })
      rid = String(data.run_id ?? '')
    } catch (e: unknown) {
      const err = e as { status?: number; runId?: string | null }
      if (err?.status === 409 && err.runId) rid = err.runId
      else throw e
    }
    if (!rid) throw new Error('没有拿到 run_id')

    // ⚠️ **要两步**（2026-11-04 修的真 bug）
    //    `POST /api/handle` 返回的是**流水线 run_id**（pipeline_run）✅
    //    ① 轮询 `GET /api/runs/{run_id}` 等 DONE
    //    ② 从 `params.agent_run_id` 拿**结果 id**，再 `GET /api/handle/{agent_run_id}`
    //    我第一版直接拿 run_id 去查 /api/handle/{id} → **agent_run 不存在** ✅ ✗
    let agentRunId = ''
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const { data: r } = await http.get<{
        status?: string
        params?: { agent_run_id?: string }
      }>(`/api/runs/${rid}`)
      if (r.status === 'FAILED' || r.status === 'DEAD') {
        explainError.value = '这次推理失败了（可能模型不可用），稍后可以重试'
        return
      }
      if (r.status === 'DONE' || r.status === 'DEGRADED') {
        agentRunId = String(r.params?.agent_run_id ?? '')
        break
      }
    }
    if (!agentRunId) {
      explainError.value = '超过 60 秒还没出结果，去系统日志看这条任务'
      return
    }
    const { data: d } = await http.get<{
      result?: { recommendation?: string; confidence?: number;
             tool?: string; needs_human?: boolean }
      steps?: Array<{ name?: string; ms?: number; ok?: boolean }>
      evidence?: Array<{ ref?: string; source?: string; text?: string }>
    }>(`/api/handle/${agentRunId}`)
    explainResult.value = { result: d.result, steps: d.steps, evidence: d.evidence }
  } catch (e: unknown) {
    explainError.value = String(e)
  } finally {
    explainLoading.value = false
  }
}


// **工具名 → 业务话术**（2026-11-04）：内部工具名不该露在界面上
//   注意 labels.ts 里有个 TOOL_LABEL 是字典（不是函数）→ 这里用 toolLabel
const TOOL_CN: Record<string, string> = {
  'cs.batch_task': '生成客服批量任务',
  'review.draft_reply': '生成客服任务单',
  'factory.feedback': '反馈工厂',
  'listing.update_desc': '修改商品描述',
  'listing.restore_desc': '回滚商品描述',
  'product.update_price': '修改商品价格',
  'hitl.create': '转人工',
}

function toolLabel(name?: string): string {
  return TOOL_CN[name || ''] || '未知动作'
}

</script>

<template>
  <div class="detail">
    <!-- 返回 -->
    <div class="detail__bar">
      <ElButton :icon="ArrowLeft" text @click="router.back">返回</ElButton>
    </div>

    <!-- **解释器**（2026-11-04）：把原来那个「智能处置」页降级成这里 ✅
         用户问「智能处置有什么业务价值」→ 它和提案生成器重复 ✅
         但它有证据链 E1/E2/E3 + 轨迹 → 改成解释「这条建议凭什么」✅ -->
    <div class="detail__explain-bar">
      <ElButton plain type="primary" size="small" @click="openExplain()">
        🧠 提案依据
      </ElButton>
      <span class="detail__explain-hint">
        展开看这个簇查了什么、依据哪几条、置信度多少
      </span>
    </div>

    <ElDrawer v-model="explainOpen" title="提案依据" size="520px">
      <div v-if="explainLoading" class="detail__ex-loading">
        正在推理（约 10 秒）—— 它会查这个簇、读 5 条原文、再看历史战绩
      </div>
      <div v-else-if="explainError" class="detail__ex-err">{{ explainError }}</div>
      <template v-else-if="explainResult">
        <div class="detail__ex-sec">
          <div class="detail__ex-label">结论</div>
          <div class="detail__ex-text">
            {{ explainResult.result?.recommendation || '（模型没给出文字建议）' }}
          </div>
          <div class="detail__ex-meta">
            置信度 {{ ((explainResult.result?.confidence ?? 0) * 100).toFixed(0) }}%
            <template v-if="explainResult.result?.tool">
              ｜ 建议动作：{{ toolLabel(explainResult.result.tool) }}
            </template>
            <template v-if="explainResult.result?.needs_human">
              ｜ **需要人工确认**
            </template>
          </div>
        </div>

        <div v-if="explainResult.evidence?.length" class="detail__ex-sec">
          <div class="detail__ex-label">它看了什么（证据）</div>
          <div v-for="(e, i) in explainResult.evidence" :key="i" class="detail__ex-ev">
            <b>{{ e.ref || ('E' + (i + 1)) }}</b>
            <span>{{ e.text || e.source }}</span>
          </div>
        </div>
      </template>
    </ElDrawer>

    <PageLoading v-if="loading" :rows="3" chart />
    <ErrorState v-else-if="error" :error="error" @retry="load" />

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

          <!-- ⚠️ 2026-11-02 改：不再逐字段 dump 后端 JSON -->
          <!--    用户反馈「太多后端 json 数据，看不懂」 -->
          <!--    现在按工具类型渲染成人话：回复草稿就给"回复正文"，改价就给"新价格" -->
          <div class="detail__section-label">要做什么（参数由代码构造，不是模型填的）</div>

          <!-- 生成回复草稿：重点是那段回复正文 -->
          <template v-if="detail.tool_name === 'review.draft_reply'">
            <div class="detail__reply">
              <div class="detail__reply-label">
              <!-- ⚠️ 2026-11-04 修：原来是「这条回复会发给买家」——错的 ✅
                   回复差评是客服的活，我们产出的是话术草稿，不自动发 ✅ -->
              这是一份客服话术草稿（不会自动发出，由客服在后台执行）：
            </div>
              <div class="detail__reply-body">{{ String(detail.args.content ?? '（空）') }}</div>
              <div v-if="complianceHits.length" class="detail__reply-flags">
                ⚠️ 合规检查命中 {{ complianceHits.length }} 处：
                <code v-for="(h, i) in complianceHits" :key="i">{{ h }}</code>
              </div>
              <div v-else class="detail__reply-ok"> 合规检查通过（无金额承诺 / 无隐私索要 / 无绝对化用语）</div>
            </div>
          </template>

          <!-- 改描述：给"新描述" + 商品 -->
          <template v-else-if="detail.tool_name === 'listing.update_desc'">
            <div class="detail__reply">
              <div class="detail__reply-label">新的商品描述：</div>
              <div class="detail__reply-body">{{ String(detail.args.description ?? '（空）') }}</div>
              <div class="detail__reply-label" style="margin-top: 8px">作用在商品：</div>
              <div>{{ String(detail.args.platform ?? '') }} / {{ String(detail.args.spu_id ?? '') }}</div>
            </div>
          </template>

          <!-- 改价格：给"新价格" -->
          <template v-else-if="detail.tool_name === 'product.update_price'">
            <div class="detail__reply">
              <div class="detail__reply-label">新价格：</div>
              <div class="detail__reply-body detail__reply-body--big">
                ¥ {{ String(detail.args.price ?? detail.args.new_price ?? '（空）') }}
              </div>
            </div>
          </template>

          <!-- 其他工具：只显示"人话字段"，不 dump 全部 -->
          <template v-else>
            <ElDescriptions :column="1" size="small" border>
              <ElDescriptionsItem v-for="(v, k) in friendlyArgs" :key="k" :label="String(k)">
                <span class="detail__arg">{{ String(v) }}</span>
              </ElDescriptionsItem>
            </ElDescriptions>
          </template>

          <!-- 技术细节：默认折叠（trace/run/tool_version/原始 args）-->
          <el-collapse class="detail__tech">
            <el-collapse-item title="技术细节（想看原始参数/链路 id 点这里）">
              <ElDescriptions :column="1" size="small" border>
                <ElDescriptionsItem label="工具版本">{{ detail.tool_version }}</ElDescriptionsItem>
                <ElDescriptionsItem label="链路 id">{{ detail.trace_id }}</ElDescriptionsItem>
                <ElDescriptionsItem label="运行 id">{{ detail.run_id }}</ElDescriptionsItem>
                <ElDescriptionsItem label="原始参数">
                  <pre class="detail__raw">{{ JSON.stringify(detail.args, null, 2) }}</pre>
                </ElDescriptionsItem>
              </ElDescriptions>
            </el-collapse-item>
          </el-collapse>

          <div class="detail__meta">
            <span>生成于 {{ new Date(detail.created_at).toLocaleString('zh-CN') }}</span>
          </div>
        </ElCard>

        <!-- ══════════ 中：依据（核心卖点）══════════ -->
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
                还有 {{ reviewTotal - reviews.length }} 条没显示（这里只列代表样本 + 最近的）
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
                  ? '批准后会真的执行；执行完代码会回查一次'
                  : detail.risk_level === 'L3'
                    ? 'L3 不自动执行，只转人工工单'
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
          <!-- ⚠️ 2026-11-02 修：用后端的 `has_pending_task` 判断能不能批 -->
          <!--    原来只看 status === PENDING_APPROVAL → 点了 404 ✗ -->
          <div class="detail__actions">
            <ElTooltip
              :disabled="canApprove"
              :content="detail.approve_blocked_reason || '这条现在不能批准'"
              placement="top"
            >
              <span>
                <ElButton type="primary" :loading="acting" :disabled="!canApprove"
                          @click="doApprove">
                  批准（会真执行）
                </ElButton>
              </span>
            </ElTooltip>
            <ElButton :disabled="acting" @click="rejectDrawer = true">
              拒绝
            </ElButton>
          </div>
          <div v-if="!canApprove && detail.approve_blocked_reason" class="detail__actions-hint is-warn">
            ⚠️ {{ detail.approve_blocked_reason }}
          </div>
          <div class="detail__actions-hint">
            批准需要 <b>supervisor</b> 角色；operator 点了会 403（UI 会先隐藏按钮）
          </div>
        </ElCard>
      </div>

      <!-- ══════════ 底部：该簇周趋势 ══════════ -->
      <ElCard shadow="never">
        <template #header>
          <span class="detail__title">该簇评价周趋势</span>
          <span class="detail__sub">用来判断"这个问题是在变好还是变坏"</span>
        </template>
        <!-- ⚠️ 空态说清为什么空（2026-11-02）-->
        <EmptyState v-if="!trend.length" compact
                    description="这个簇还没有周趋势数据"
                    hint="按周统计需要这个簇的评价跨越多周；如果都集中在同一周，就没有趋势可画" />
        <VChart v-else class="detail__trend" :option="trendOption" autoresize />
      </ElCard>
    </template>

    <!-- 拒绝抽屉（必须带原因）-->
    <ElDrawer v-model="rejectDrawer" title="拒绝这条提案" size="420px">
      <div class="detail__drawer-hint">拒绝必须写原因（后端 `min_length=1`，空原因会 422）</div>
      <el-input v-model="rejectReason" type="textarea" :rows="5"
                placeholder="例如：这个方案不符合成本要求 / 已经线下处理过了" />
      <div class="detail__drawer-actions">
        <ElButton @click="rejectDrawer = false">取消</ElButton>
        <ElButton type="danger" :loading="acting" :disabled="!rejectReason.trim()"
                  @click="doReject">确认拒绝</ElButton>
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

.detail {
  &__explain-bar {
    display: flex;
    align-items: center;
    gap: $space-sm;
    margin-bottom: $space-sm;
  }

  &__explain-hint {
    font-size: 11px;
    color: var(--hub-text-muted);
  }

  &__ex-loading {
    padding: $space-lg 0;
    font-size: 13px;
    line-height: 1.8;
    color: var(--hub-text-muted);
  }

  &__ex-err {
    padding: $space-md;
    font-size: 13px;
    color: var(--el-color-danger);
    background: var(--el-color-danger-light-9);
    border-radius: $radius-base;
  }

  &__ex-sec {
    margin-bottom: $space-lg;
  }

  &__ex-label {
    margin-bottom: $space-sm;
    font-size: 12px;
    font-weight: 600;
    color: var(--hub-text-muted);
  }

  &__ex-text {
    font-size: 14px;
    line-height: 1.8;
  }

  &__ex-meta {
    margin-top: 4px;
    font-size: 11px;
    color: var(--hub-text-muted);
  }

  &__ex-ev {
    display: flex;
    gap: 8px;
    padding: 6px 0;
    font-size: 12px;
    line-height: 1.7;
    border-bottom: 1px dashed var(--hub-border-light);

    b {
      flex: 0 0 auto;
      color: var(--el-color-primary);
    }
  }

  &__ex-step {
    padding: 4px 0;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--hub-text-muted);
  }
}
</style>
