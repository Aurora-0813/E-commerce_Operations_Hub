// ══════════════════════════════════════════════════════════════
//  数据接口（图表 / 提案 / 审计 / 簇评价）
//  ⚠️ **每一个字段都是 2026-11-01 对着真实接口验过的** ✅
//     （上一轮我按想象写 `tiers: [{tier, n}]`，实际是 `{items:[{tier, clusters}]}`）
//  ⚠️ 图表类**统一是 `{items: [...]}`**（已验证 7 个）
// ══════════════════════════════════════════════════════════════
import http from './client'

// ══════════════════ 图表 ══════════════════
export interface TierItem { tier: string; clusters: number; reviews: number }
export interface ClusterRow {
  id: string; label: string; reviews: number; tier: string
  score: number; domain: string; spus: number
}
export interface TopicItem { topic: string; reviews: number; share: number }
export interface GmvItem { spu_id: string; title: string; gmv: number; units: number }
export interface PricePoint {
  spu_id: string; title: string; platform: string
  price: number; review_rate: number; review_count: number
}
export interface TrendPoint {
  date: string
  total?: number
  bad?: number
  /** 0~1 的小数（前端乘 100 显示成 % ✅）
   *  ⚠️ 字段名是 bad_rate，不是 rate（2026-11-04 修）*/
  bad_rate?: number
}
/**
 * 簇的周趋势
 * ⚠️ **字段名是 `reviews`，不是 `n`**（2026-11-02 修）
 *    我原来写 `n` → `d.n` 全是 undefined → 柱子高度 0 → 看起来「没有图」✅ ✗
 */
export interface WeekPoint { week: string; reviews: number }

/** P 档环形 */
export async function getClusterTiers(): Promise<TierItem[]> {
  const { data } = await http.get<{ items: TierItem[] }>('/api/charts/cluster-tiers')
  return data.items ?? []
}

/** 簇条数（表格 + 条形同源）*/
export async function getClusters(sort: 'reviews' | 'priority' | 'label' = 'reviews'): Promise<ClusterRow[]> {
  const { data } = await http.get<{ items: ClusterRow[] }>('/api/charts/clusters', {
    params: { sort },
  })
  return data.items ?? []
}

/** 问题类型饼 */
export async function getTopicDistribution(): Promise<TopicItem[]> {
  const { data } = await http.get<{ items: TopicItem[] }>('/api/charts/topic-distribution')
  return data.items ?? []
}

/** GMV TopN */
export async function getGmvTop(n = 10): Promise<GmvItem[]> {
  const { data } = await http.get<{ days: number; n: number; items: GmvItem[] }>(
    '/api/charts/gmv-top', { params: { n } })
  return data.items ?? []
}

/** 价格 vs 差评率散点 */
export async function getPriceVsBadrate(): Promise<PricePoint[]> {
  const { data } = await http.get<{ items: PricePoint[] }>('/api/charts/price-vs-badrate')
  return data.items ?? []
}

/** 差评率趋势（⚠️ 现在**可能是空数组** —— 数据还在造 ✅）*/
export async function getBadRateTrend(days = 7): Promise<TrendPoint[]> {
  const { data } = await http.get<{ platform: string; days: number; items: TrendPoint[] }>(
    '/api/charts/bad-rate-trend', { params: { days } })
  return data.items ?? []
}

/** 某簇周趋势 */
export async function getClusterTrend(clusterId: string): Promise<WeekPoint[]> {
  const { data } = await http.get<{ items: WeekPoint[]; error?: string }>(
    '/api/charts/cluster-trend', { params: { cluster_id: clusterId } })
  return data.items ?? []
}

/** 簇四因子（**只给真实输入 + note**，后端故意不归一化 ✅）*/
export interface FactorsResp {
  cluster_id: string
  label: string
  inputs: {
    review_count: number
    severity: number | null
    growth_rate: number | null
    priority_score: number | null
    priority_tier: string
  }
  note: string
}
export async function getFactors(clusterId: string): Promise<FactorsResp> {
  const { data } = await http.get<FactorsResp>(`/api/clusters/${clusterId}/factors`)
  return data
}

/** 复盘（⚠️ `effect`/`conclusion` 现在**全是 null** —— 观察窗口未到 ✅）*/
export interface RetroItem {
  proposal_id: string
  status: string
  effect: number | null
  conclusion: 'POSITIVE' | 'NEGATIVE' | 'INCONCLUSIVE' | null
  confidence: number | null
  reason_code: string | null
  baseline: [string, string] | null
  observe_end: string | null
  metrics_before: Record<string, number> | null
  metrics_after: Record<string, number> | null
}
/**
 * 复盘列表
 * ⚠️ **返回 `{total, items}`**（2026-11-02 修）
 *    原来只返回 `items` ✅ → 前端拿 `items.length` 当"多少个动作在等窗口"
 *    → 而接口 `limit=50` → **531 个被显示成 50** ✅ ✗
 */
export async function getRetro(limit = 200): Promise<{ total: number; items: RetroItem[] }> {
  const { data } = await http.get<{ total?: number; items: RetroItem[] }>(
    '/api/retro', { params: { limit } })
  return { total: data.total ?? (data.items?.length ?? 0), items: data.items ?? [] }
}

// ══════════════════ 提案 ══════════════════
export interface ProposalRow {
  id: string
  tool_name: string
  risk_level: string
  status: string
  topic: string | null
  rationale: string
  created_at: string
}

export interface ProposalDetail extends ProposalRow {
  run_id: string
  trace_id: string
  tool_version: string
  args: Record<string, unknown>
  /** ⚠️ 只有 `[{issue_cluster: uuid}]` —— **原文要靠 `/clusters/{id}/reviews`** ✅ */
  evidence_refs: Array<{ issue_cluster?: string }>
  dry_run: boolean
  /**
   * **这条提案有没有待批任务**（2026-11-02 加）
   * `true` 才能批准；`false`/`null` 说明没提交网关 → 前端要禁用按钮 ✅
   */
  has_pending_task?: boolean | null
  /** 不能批准时的人话原因（后端给的 ✅）*/
  approve_blocked_reason?: string | null
  experience: Array<{ tool?: string; topic?: string; positive?: number; negative?: number }>
}

export interface ProposalListParams {
  status?: string
  topic?: string
  limit?: number
  offset?: number
  /** **只看这个簇的提案**（2026-11-04 加 ✅）*/
  cluster_id?: string
}
export interface Page<T> { total: number; limit: number; offset: number; items: T[] }

export async function getProposals(p: ProposalListParams = {}): Promise<Page<ProposalRow>> {
  const { data } = await http.get<Page<ProposalRow>>('/api/proposals', { params: p })
  return data
}

export async function getProposal(id: string): Promise<ProposalDetail> {
  const { data } = await http.get<ProposalDetail>(`/api/proposals/${id}`)
  return data
}

// ══════════════════ 簇的评价原文（"依据展开" ✅ 2026-11-01 补的接口）══════════════════
export interface ClusterReview {
  record_id: string
  platform: string
  platform_spu_id: string
  rating: number
  review_time: string | null
  /** **原文**（入库时已 PII 脱敏 ✅）*/
  content: string
  topic: string | null
  severity: number | null
  /** 代表样本（**排序时代表样本在前** ✅）*/
  is_representative: boolean
}
export interface ClusterReviewsPage {
  cluster_id: string
  label: string
  total: number
  limit: number
  offset: number
  items: ClusterReview[]
}

export async function getClusterReviews(
  clusterId: string,
  p: { limit?: number; offset?: number; only_negative?: boolean } = {},
): Promise<ClusterReviewsPage> {
  const { data } = await http.get<ClusterReviewsPage>(
    `/api/clusters/${clusterId}/reviews`, { params: p })
  return data
}

// ══════════════════ 审计 ══════════════════
export interface AuditRow {
  id: number
  at: string
  actor: string
  action: string
  resource: string
  detail: Record<string, unknown> | null
  ip: string | null
}
export async function getAudit(p: { action?: string; actor?: string; limit?: number; offset?: number } = {}): Promise<Page<AuditRow>> {
  const { data } = await http.get<Page<AuditRow>>('/api/audit', { params: p })
  return data
}
