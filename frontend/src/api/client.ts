// ══════════════════════════════════════════════════════════════
//  axios 封装（唯一 HTTP 入口）
//  ⚠️ 全项目**禁止**用 fetch / XHR 发请求 —— 只用这里的 `http` ✅
//  ⚠️ 所有请求都是 `/api/xxx`（**不含 host**）→ 开发走 vite 代理，生产同源
// ══════════════════════════════════════════════════════════════
import axios, {
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'

/** 后端统一错误体（每个错误都带 trace_id ✅） */
export interface ApiErrorBody {
  code?: string
  message?: string
  trace_id?: string
  run_id?: string | null
}

/** 抛给业务层的错误（**不要把 axios 的原始 Error 直接扔出去** ✅） */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly traceId: string
  /** 409 时后端会带"已有的 run_id"（幂等，不是失败 ✅） */
  readonly runId: string | null

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `请求失败（HTTP ${status}）`)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || ''
    this.traceId = body.trace_id || ''
    this.runId = body.run_id || null
  }
}

// ---------- token 存取（**不引 Pinia**，避免 store ↔ axios 循环依赖 ✅）----------
const TOKEN_KEY = 'hub_token'
const USER_KEY = 'hub_user'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/** 未登录/过期时，router 会注册一个回调过来（**不在 axios 里直接 import router** ✅ 防循环依赖）*/
type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null
export function setUnauthorizedHandler(fn: UnauthorizedHandler): void {
  onUnauthorized = fn
}

/** 全局错误提示（由 main.ts 注入 ElMessage，避免 axios 依赖 UI 库 ✅）*/
type Notifier = (message: string) => void
let notify: Notifier = () => {}
export function setNotifier(fn: Notifier): void {
  notify = fn
}

/** 生成一个前端 trace_id（方便和后端日志对账 ✅）*/
function newTraceId(): string {
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

const http: AxiosInstance = axios.create({
  // 走 vite 代理 → 同源；生产也是同源 ✅
  baseURL: '/',
  timeout: 120_000, // 后端 agent 任务 3~10 秒；全链提交是 202 立即返回；120s 很宽裕
  headers: { 'Content-Type': 'application/json' },
})

// ══════════════════ 请求拦截：注入 token + trace_id ══════════════════
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  config.headers.set('X-Trace-Id', newTraceId())
  return config
})

// ══════════════════ 响应拦截：把错误翻译成人话 ══════════════════
http.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<ApiErrorBody>) => {
    // 网络层（后端没起、断网）
    if (!err.response) {
      const msg = '连不上后端 —— 确认 8000 端口的服务在跑 ✅'
      notify(msg)
      return Promise.reject(new ApiError(0, { message: msg, code: 'NETWORK' }))
    }

    const status = err.response.status
    const body = (err.response.data || {}) as ApiErrorBody

    // ---------- 401：token 过期/被撤销 → 跳登录 ✅ ----------
    //   ⚠️ token 只有 **3 小时**，会过期；后端登出会把它写进 Redis 黑名单
    if (status === 401) {
      clearToken()
      onUnauthorized?.()
      // 401 **不弹 toast**（跳登录已经说明了），避免页面上闪一下红条
      return Promise.reject(new ApiError(status, body))
    }

    // ---------- 403：角色不够 → **不跳登录**（是权限，不是登录问题 ✅）----------
    if (status === 403) {
      notify(body.message || '权限不够（需要更高角色）')
      return Promise.reject(new ApiError(status, body))
    }

    // ---------- 409：**我们的幂等**，不当红错弹 ✅ ----------
    //   · POST /api/runs 409 + run_id → 已有任务，直接跳去轮询
    //   · POST /api/handle 409 + run_id → 同簇已有任务
    //   · POST /api/knowledge/{id}/publish 409 → 已发布过
    //   → **由调用方处理**（常见做法：ElMessage.info 而不是 error）
    if (status === 409) {
      return Promise.reject(new ApiError(status, body))
    }

    // ---------- 422：字段校验失败（如"拒绝必须带原因"）→ 由表单显示 ----------
    if (status === 422) {
      return Promise.reject(new ApiError(status, {
        ...body,
        message: body.message || '参数校验没通过',
      }))
    }

    // ---------- 413：上传超 20MB ----------
    if (status === 413) {
      notify('文件太大（上限 20MB），请按月拆包后再传 ✅')
      return Promise.reject(new ApiError(status, body))
    }

    // ---------- 503：Redis 连不上（后端不谎报成功 ✅）----------
    if (status === 503) {
      notify(body.message || '服务暂时不可用（队列/缓存不可达）')
      return Promise.reject(new ApiError(status, body))
    }

    // ---------- 其他：显示 message + trace_id ----------
    const tid = body.trace_id ? `（trace: ${body.trace_id}）` : ''
    notify(`${body.message || `请求失败（HTTP ${status}）`}${tid}`)
    return Promise.reject(new ApiError(status, body))
  },
)

export default http
