// ⚠️ **自动生成 — 不要手改**（2026-11-04）
// 来源：FastAPI `/api/charts/*` 的**真实响应 JSON**
// 为什么不用 openapi.json：这 9 个端点**没声明返回模型** → schema 是空的
// 重新生成：python backend/scripts/gen_api_types.py
//
// **背景**：我靠猜字段名错了 3 次（n→reviews / created→PENDING / rate→bad_rate）
//   每次都是「图是空的」，而且编译期抓不到 → 所以改成**从真实响应生成** ✅

/** `GET /api/charts/bad-rate-trend?days=30` 的列表项 */
export interface BadRateTrendItem { date: string; total: number; bad: number; bad_rate: number }

/** `GET /api/charts/bad-rate-trend?days=30` */
export interface BadRateTrendResp { platform: string; days: number; items: BadRateTrendItem[] }

/** `GET /api/charts/cluster-tiers` 的列表项 */
export interface ClusterTiersItem { tier: string; clusters: number; reviews: number }

/** `GET /api/charts/cluster-tiers` */
export interface ClusterTiersResp { run_id: string; items: ClusterTiersItem[] }

/** `GET /api/charts/clusters` 的列表项 */
export interface ClustersItem { id: string; label: string; reviews: number; tier: string; score: number; domain: string; spus: number }

/** `GET /api/charts/clusters` */
export interface ClustersResp { run_id: string; items: ClustersItem[] }

/** `GET /api/charts/gmv-top?n=10` 的列表项 */
export interface GmvTopItem { spu_id: string; title: string; gmv: number; units: number }

/** `GET /api/charts/gmv-top?n=10` */
export interface GmvTopResp { days: number; n: number; items: GmvTopItem[] }

/** `GET /api/charts/hitl-trend?days=30` */
export type HitlTrendResp = { days: number; items: unknown[] }

/** `GET /api/charts/observe-timeline` 的列表项 */
export interface ObserveTimelineItem { proposal_id: string; status: string; observe_end: string; conclusion: null; effect: null }

/** `GET /api/charts/observe-timeline` */
export interface ObserveTimelineResp { items: ObserveTimelineItem[] }

/** `GET /api/charts/price-vs-badrate` 的列表项 */
export interface PriceVsBadRateItem { spu_id: string; title: string; platform: string; price: number; review_rate: number; review_count: number }

/** `GET /api/charts/price-vs-badrate` */
export interface PriceVsBadRateResp { items: PriceVsBadRateItem[] }

/** `GET /api/charts/topic-distribution` 的列表项 */
export interface TopicDistributionItem { topic: string; reviews: number; share: number }

/** `GET /api/charts/topic-distribution` */
export interface TopicDistributionResp { items: TopicDistributionItem[] }
