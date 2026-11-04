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
/**
 * 上传图片包 → 处理 → 结果
 *
 * ⚠️ **2026-11-03 改：默认走「入队异步」**
 *   同步模式下 428 张 x 8.9 秒 = **63 分钟**，HTTP 必超时 ✅ ✗
 *   （实测 3 张图 0.3 秒，所以同步"看起来"没问题 —— 是个假象 ✅）
 *
 * 现在的流程：
 *   上传 → 后端存文件 + 入队 → **202 + run_id**（秒回 ✅）
 *   前端**轮询** `GET /api/runs/{run_id}` 直到 DONE/FAILED
 *   把 `params.mm` 的统计转成 `ImageIngestResult` 返回（**组件不用改** ✅）
 *
 * 同包重复传：后端按整包 sha256 判重 → **409（带已有 run_id）** ✅ 幂等
 */
export async function ingestImages(
  file: File, vision: 'real' | 'mock' = 'real', force = true,
  onProgress?: (percent: number) => void,
): Promise<ImageIngestResult> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('vision', vision)
  fd.append('force', force ? 'true' : 'false')
  // ⚠️ **必须是 queue**（sync 只给小包/测试用 ✅）
  fd.append('mode', 'queue')

  onProgress?.(5)
  const { data } = await http.post<{
    run_id: string; kind: string; status: string; sha256: string
  }>('/api/ingest/images', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180_000,
  })
  onProgress?.(20)

  const rid = data.run_id
  const deadline = Date.now() + 3 * 60 * 60 * 1000     // 3 小时（真包 63 分钟 ✅）
  let pct = 20
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000))
    const { data: run } = await http.get<{
      status: string; error?: string | null
      params?: { mm?: Record<string, unknown> }
    }>(`/api/runs/${rid}`)

    if (run.status === 'DONE' || run.status === 'FAILED' || run.status === 'DEAD') {
      const mm = (run.params?.mm ?? {}) as Record<string, unknown>
      onProgress?.(100)
      return {
        images: Number(mm.images ?? 0),
        processed: Number(mm.processed ?? 0),
        skipped: Number(mm.skipped ?? 0),
        deduped: Number(mm.deduped ?? 0),
        failed: Number(mm.failed ?? 0),
        stripped: Number(mm.stripped ?? 0),
        destroyed: Number(mm.destroyed ?? 0),
        residual: Number(mm.residual ?? 0),
        elapsed_s: Number(mm.elapsed_s ?? 0),
        vision: String(mm.vision ?? vision),
      } as ImageIngestResult
    }
    pct = Math.min(95, pct + 2)
    onProgress?.(pct)
  }
  throw new Error('处理超时（3 小时）—— 去「运维台」看这条 run 的状态 ✅')
}

/**
 * 图片包**预检**（只对账，不处理 ✅）
 *
 * **为什么必须先预检**
 *   真实包 428 张 x 8.9 秒 ≈ **63 分钟**；如果先跑再发现"只匹配上 1 条"，
 *   用户就白等了一小时。
 *   → 这个接口**不解压、不调模型、秒回**，先让用户看清对账再决定跑不跑。
 */
export interface PrecheckResp {
  package_files: number
  manifest_source: string
  manifest_rows: number
  name_collisions: number
  reviews_with_image: number
  reviews_matched: number
  reviews_unmatched: number
  images_matched: number
  images_unused: number
  by_tier: Record<string, number>
  tier_text: string
  unmatched_sample: Array<{ review_id: string; expected_files: string[] }>
  unused_sample: string[]
  missing_sample: Array<{ review_id: string; expected: number; found: number }>
  est_calls: number
  est_seconds: number
  verdict: string
  file: string
  bytes: number
}

export async function precheckImages(
  file: File,
  allowLoose = false,
): Promise<PrecheckResp> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('allow_loose', String(allowLoose))
  const { data } = await http.post<PrecheckResp>('/api/ingest/images/precheck', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60_000,
  })
  return data
}

/**
 * **停止**一条正在跑的链（2026-11-04 加）
 *
 * 用户诉求："开始分析后要有停止任务按钮，**真的在后端停止**" ✅
 * 后端：`POST /api/runs/{id}/cancel` → status 改 `CANCELLED`
 *       → worker **每个阶段结束时**查一次，看到就退出 ✅
 * 局限（如实说）：当前**正在跑的那个阶段**不会被打断，它跑完才停 ✅
 */
export async function cancelRun(runId: string): Promise<{ ok: boolean; message?: string }> {
  const { data } = await http.post<{ ok: boolean; message?: string }>(
    `/api/runs/${runId}/cancel`)
  return data
}

/**
 * **删除**一条任务（连同它的簇和提案）
 *
 * 后端：`DELETE /api/runs/{id}`
 *   → 删 `pipeline_run` + 这次跑出来的 `issue_cluster` + `action_proposal`
 *   ⚠️ **不删**原始业务数据（record / sales_daily / image_asset）✅
 */
export async function deleteRun(
  runId: string,
): Promise<{ ok: boolean; deleted?: { clusters: number; proposals: number }; message?: string }> {
  const { data } = await http.delete<{
    ok: boolean
    deleted?: { clusters: number; proposals: number }
    message?: string
  }>(`/api/runs/${runId}`)
  return data
}
