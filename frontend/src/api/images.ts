// ══════════════════════════════════════════════════════════════
//  图片明细 / 人工复核（api/images.ts）—— 2026-11-03 新增
// ══════════════════════════════════════════════════════════════
// **为什么要有复核**（真实业务红线）
//   AI 打的标**不能直接进赔偿决策** ✅
//   → 低置信度 / 高风险缺陷 → 人看一眼
//   → 人工结果落 `final_defect`，**合并时优先用它**
//   → 复核数据**就是下一次微调的标注集** ✅
// ══════════════════════════════════════════════════════════════
import http from './client'

/** 缺陷类型（和后端 `ALLOWED_DEFECTS` 一致 ✅）*/
export const DEFECT_OPTIONS = ['none', '破损', '开线', '色差', '污渍', '其他'] as const
export type Defect = (typeof DEFECT_OPTIONS)[number]

/** 一张图的明细 */
export interface ImageAssetRow {
  id: string
  review_id: string
  /** 评价的平台 id（`TB00000008`）*/
  platform_review_id: string | null
  /** 这条评价的原文（截断 120 字 ✅）*/
  review_text: string
  /** **原文件名** —— 复核时能追到"这是包里哪张图" ✅ */
  source_file: string | null
  /** 用了哪一级匹配（manifest / url_basename / directory / filename / loose）*/
  match_tier: string | null
  defect_type: string | null
  severity: number | null
  confidence: number | null
  color_deviation: number | null
  vision_model: string | null
  degraded: boolean
  /** 复核人（`null` = 还没复核 ✅）*/
  reviewed_by: string | null
  reviewed_at: string | null
  /** **人工最终判定**（合并 review_feature 时优先用它 ✅）*/
  final_defect: string | null
  review_note: string | null
  created_at: string | null
}

/** 复核队列概况 */
export interface ImageStats {
  total: number
  pending: number
  /** 置信度低于阈值的（要人看 ✅）*/
  low_confidence: number
  /** 高风险缺陷（破损 / 开线）*/
  high_risk: number
  threshold: number
}

export interface ListImagesParams {
  pending_only?: boolean
  max_confidence?: number
  defect?: string
  limit?: number
}

/** 列图片明细（**默认只看待复核的** ✅）*/
export async function listImages(p: ListImagesParams = {}): Promise<ImageAssetRow[]> {
  const { data } = await http.get<{ items: ImageAssetRow[] }>('/api/images', {
    params: { pending_only: p.pending_only ?? true, ...p },
  })
  return data.items ?? []
}

/** 复核队列概况 */
export async function getImageStats(): Promise<ImageStats> {
  const { data } = await http.get<ImageStats>('/api/images/stats')
  return data
}

/**
 * 人工复核一张图
 *
 * ⚠️ **副作用**：后端会**重算这条评价的 `review_feature`**（人工优先 ✅）
 *    所以复核完不用手动刷新合并结果 ✅
 */
export async function reviewImage(
  id: string,
  finalDefect: Defect,
  note?: string,
): Promise<{ ok: boolean; review_id: string; reaggregated: unknown }> {
  const { data } = await http.post<{ ok: boolean; review_id: string; reaggregated: unknown }>(
    `/api/images/${id}/review`,
    { final_defect: finalDefect, note: note || null },
  )
  return data
}

/** 撤销复核（回到模型结果）—— **supervisor 及以上** ✅ */
export async function unreviewImage(id: string): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(`/api/images/${id}/unreview`)
  return data
}
