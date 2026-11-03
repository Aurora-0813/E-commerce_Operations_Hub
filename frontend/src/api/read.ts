// ══════════════════════════════════════════════════════════════
//  读接口（本批用到的 6 个）
//  ⚠️ 全部走 `http`（axios 封装）—— **不用原生 fetch** ✅
//  ⚠️ 类型：字段是按 `docs/API.md` 和 `src/types/api.ts` 写的；
//     不确定的标 `?`，**宁可宽松也不要编字段** ✅
// ══════════════════════════════════════════════════════════════
import http from './client'

// ---------- GET /api/today ----------
/** "要我做" 的一条（待批 / 到期复盘 / 死信 / agent 转人工）*/
export interface TodayItem {
  kind: string
  title: string
  hint?: string
  /** 关联资源（点进去用）*/
  resource_id?: string
}

export interface TodayResp {
  /** 🔴 要我做 */
  must_do: TodayItem[]
  /** ⏳ 等观察窗口 */
  waiting: TodayItem[]
  /** 🔵 知会（已出结论等）*/
  notice?: TodayItem[]
  /** 异常（如果有）*/
  anomalies?: TodayItem[]
}

export async function getToday(): Promise<TodayResp> {
  const { data } = await http.get<TodayResp>('/api/today')
  return data
}

// ---------- GET /api/status ----------
/** 业务数据现状（13 项，字段较多 → 用宽松类型 ✅）*/
export type StatusResp = Record<string, number | string | null>

export async function getStatus(): Promise<StatusResp> {
  const { data } = await http.get<StatusResp>('/api/status')
  return data
}

// ---------- GET /api/health ----------
export interface HealthCheck {
  name: string
  ok: boolean
  detail?: string
}

export interface HealthResp {
  ok: boolean
  checks: HealthCheck[]
}

export async function getHealth(): Promise<HealthResp> {
  const { data } = await http.get<HealthResp>('/api/health')
  return data
}

// ---------- GET /api/brief ----------
export interface BriefResp {
  /** ⚠️ **可能 false**（还没生成过）→ 走空态，不要塞假数据 ✅ */
  available: boolean
  generated_at?: string
  /** 纯文本版（多行）*/
  text?: string
  /** ✅ **真实字段就是这个**（不是 bullets）—— 按行拆好的数组 */
  lines?: string[]
  /**
   * **来源指标**（2026-11-03 加）
   * 3 组：规模与影响 / 处理漏斗 / 数据质量
   * ⚠️ 每一项都是**人话 + 数字**（比如 `评价总数 = 7020 条（保留 4115）`）
   */
  sections?: Array<{ title: string; items: Array<{ label: string; value: string }> }>
}

export async function getBrief(): Promise<BriefResp> {
  const { data } = await http.get<BriefResp>('/api/brief')
  return data
}

// ---------- GET /api/eval/latest ----------
export interface EvalResp {
  run_id?: string
  passed?: number
  total?: number
  soft_passed?: number
  exit_code?: number
}

export async function getEvalLatest(): Promise<EvalResp> {
  const { data } = await http.get<EvalResp>('/api/eval/latest')
  return data
}

// ---------- GET /api/notify ----------
export interface NotifyItem {
  id: number
  event_type: string
  resource_id: string
  channel: string
  status: 'PENDING' | 'SENT' | 'SKIPPED' | 'FAILED' | 'READ'
  attempts?: number
  payload?: Record<string, unknown>
  created_at?: string
  sent_at?: string | null
}

export interface NotifyResp {
  total: number
  /** 🔔 铃铛上的数字（**非 READ 的条数** ✅）*/
  unread: number
  items: NotifyItem[]
}

export async function getNotify(limit = 20): Promise<NotifyResp> {
  const { data } = await http.get<NotifyResp>('/api/notify', { params: { limit } })
  return data
}
