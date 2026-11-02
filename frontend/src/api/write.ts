// ══════════════════════════════════════════════════════════════
//  写接口（15 个）
//  ⚠️ **409 是幂等，不是失败** ✅ —— 调用方要按 `ApiError.runId` 处理
//  ⚠️ 危险操作必须 `confirm: true`（approve / dlq replay）
// ══════════════════════════════════════════════════════════════
import http from './client'

// ══════════════════ 提交异步任务 ══════════════════
export interface RunAccepted { run_id: string; trace_id?: string; status: string }
export interface HandleAccepted extends RunAccepted { cluster_id: string }

/** 提交全链（重复提交 → 409 + `runId`）*/
export async function postRun(kind = 'full'): Promise<RunAccepted> {
  const { data } = await http.post<RunAccepted>('/api/runs', { kind })
  return data
}

/** 提交 agent 任务（同簇重复 → 409 + `runId`，**这是幂等** ✅）*/
export async function postHandle(clusterId: string): Promise<HandleAccepted> {
  const { data } = await http.post<HandleAccepted>('/api/handle', { cluster_id: clusterId })
  return data
}

export interface RunStatus {
  run_id: string
  kind: string
  status: 'QUEUED' | 'RUNNING' | 'DONE' | 'DEGRADED' | 'FAILED' | 'DEAD'
  current_stage: string | null
  error: string | null
  params: Record<string, unknown>
  /** DONE 之后才有 → 用它再取 `/api/handle/{id}` ✅ */
  agent_run_id: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}
export async function getRun(runId: string): Promise<RunStatus> {
  const { data } = await http.get<RunStatus>(`/api/runs/${runId}`)
  return data
}

// ══════════════════ 审批 ══════════════════
/** 批准（**supervisor + confirm**）→ 404 = 没有待批任务（已批过）*/
export async function approveProposal(proposalId: string, confirm = true, note = ''): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(
    `/api/proposals/${proposalId}/approve`, { confirm, note })
  return data
}

/** 拒绝（**必须带原因**，空 → 422）*/
export async function rejectProposal(proposalId: string, reason: string): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(
    `/api/proposals/${proposalId}/reject`, { reason })
  return data
}

/** 为某簇生成提案（**只处理这个簇** ✅）*/
export async function proposeForCluster(clusterId?: string): Promise<{
  clusters: number; proposals: number; ids: string[]
}> {
  const { data } = await http.post<{ clusters: number; proposals: number; ids: string[] }>(
    '/api/propose', null, { params: clusterId ? { cluster_id: clusterId } : {} })
  return data
}

// ══════════════════ agent 采纳 ══════════════════
/** 采纳 → 生成提案（已采纳过 → 返回原 id，**幂等** ✅）*/
export async function adoptRun(runId: string): Promise<{
  ok: boolean; proposal_id: string; already_adopted: boolean
}> {
  const { data } = await http.post<{ ok: boolean; proposal_id: string; already_adopted: boolean }>(
    `/api/handle/${runId}/adopt`, { confirm: true })
  return data
}

/** 不采纳（带原因，只写审计）*/
export async function rejectRun(runId: string, reason: string): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(
    `/api/handle/${runId}/reject`, { reason })
  return data
}

// ══════════════════ 复盘 / 通知 / 知识 ══════════════════
/** 重新复盘（`only_due=true` 时不调 LLM ✅）*/
export async function rerunRetro(onlyDue = true): Promise<{ done: number; skipped: number }> {
  const { data } = await http.post<{ done: number; skipped: number }>(
    '/api/retro', null, { params: { only_due: onlyDue } })
  return data
}

/** 标记已读（表里**没有 read_at**，用 status=READ ✅）*/
export async function readNotify(id: number): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(`/api/notify/${id}/read`)
  return data
}

export interface KnowledgeCreate { title: string; content: string; topic?: string | null }
/** 新建知识条目 → **DRAFT（不生效）** ✅ */
export async function createKnowledge(body: KnowledgeCreate): Promise<{ ok: boolean; id: string; status: string }> {
  const { data } = await http.post<{ ok: boolean; id: string; status: string }>(
    '/api/knowledge', body)
  return data
}

/** 发布（**supervisor**）→ PUBLISHED + version+1 → **立即注入提示词** ✅ */
export async function publishKnowledge(id: string): Promise<{ ok: boolean; version: number }> {
  const { data } = await http.post<{ ok: boolean; version: number }>(
    `/api/knowledge/${id}/publish`)
  return data
}

// ══════════════════ 运维（危险）══════════════════
/** 重放死信（**supervisor + confirm**）*/
export async function replayDlq(runIdPrefix: string): Promise<{ ok: boolean; exit_code: number }> {
  const { data } = await http.post<{ ok: boolean; exit_code: number }>(
    '/api/dlq/replay', { confirm: true, run_id_prefix: runIdPrefix })
  return data
}

// ══════════════════ 上传 ══════════════════
export interface IngestResult {
  ok: boolean
  batch_id: string
  /** **true = 这个文件导过了（同一个 SHA256）** —— 不是错误 ✅ */
  existed: boolean
  file: string
  bytes: number
  platform: string
  data_type: string
  rows_ok: number
  rows_bad: number
  warnings: string[]
  message: string
}
/**
 * 上传平台导出文件
 * ⚠️ **用 FormData，Content-Type 交给浏览器自动带 boundary** ✅
 *    手写 `multipart/form-data` 会丢 boundary → 后端解析失败 ✅ ✗
 */
export async function ingestFile(
  file: File, platform: 'taobao' | 'jd', dataType: 'review' | 'product' | 'sales',
  onProgress?: (percent: number) => void,
): Promise<IngestResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('platform', platform)
  fd.append('data_type', dataType)
  const { data } = await http.post<IngestResult>('/api/ingest', fd, {
    headers: { 'Content-Type': undefined as unknown as string },
    timeout: 300_000,
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
  return data
}

/** 导出 xlsx 的下载地址（直接 `window.open` 即可，**走浏览器的 cookie/header 由 axios 处理不了** ✅）
 *  ⚠️ 因为要带 Authorization，**不能**简单 `window.open` → 用下面这个 `downloadExport` ✅ */
export async function downloadExport(kind: 'reviews' | 'clusters' | 'retro'): Promise<{
  blob: Blob; rows: number; piiRedacted: number
}> {
  const resp = await http.get(`/api/export/${kind}`, { responseType: 'blob' })
  return {
    blob: resp.data as Blob,
    rows: Number(resp.headers['x-export-rows'] ?? 0),
    piiRedacted: Number(resp.headers['x-export-pii-redacted'] ?? 0),
  }
}

// ══════════════════ 图片包上传（M2 多模态）══════════════════
export interface ImageIngestResult {
  ok: boolean
  file: string
  bytes: number
  /** `real` = 本地视觉模型 / `mock` = 假标签（只测连通性 ✅）*/
  vision: string
  trace_id: string
  /** 包里有几张图 */
  images: number
  /** pHash 去重掉几张 */
  deduped: number
  /** 成功处理几条评价 */
  processed: number
  /** 图不在包内 → 跳过（**这不是错误** ✅）*/
  skipped: number
  failed: number
  /** 元数据剥离（GPS/设备信息）*/
  stripped: number
  /** 覆写销毁几个文件 */
  destroyed: number
  /** ⚠️ **必须是 0**（沙箱残留）*/
  residual: number
  elapsed_s: number
  message: string
}

/**
 * 上传**图片包 zip** → 跑 M2 多模态
 * ⚠️ 语义和 `ingestFile` 不同：这是**给已有评价补打标**，不新增评价 ✅
 * ⚠️ 只接受 .zip，≤1GB（真实 images.zip 471MB ✅）
 */
export async function ingestImages(
  file: File, vision: 'real' | 'mock' = 'real', force = true,
  onProgress?: (percent: number) => void,
): Promise<ImageIngestResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('vision', vision)
  fd.append('force', force ? 'true' : 'false')
  const { data } = await http.post<ImageIngestResult>('/api/ingest/images', fd, {
    headers: { 'Content-Type': undefined as unknown as string },
    timeout: 1_800_000, // 图片包 + 真模型可能几分钟 ✅
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  })
  return data
}
