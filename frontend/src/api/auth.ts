// ══════════════════════════════════════════════════════════════
//  认证接口（3 个）
//  ⚠️ 全部走 `client.ts` 的 `http`（**不用原生 fetch** ✅）
// ══════════════════════════════════════════════════════════════
import http from './client'

export interface LoginResp {
  access_token: string
  token_type: string
  /** 秒（后端默认 10800 = 3 小时 ✅）*/
  expires_in: number
  user: {
    id: string
    email: string
    display_name: string
    role: 'analyst' | 'operator' | 'supervisor' | 'admin'
  }
}

/** `POST /api/auth/login` —— 失败统一话术（后端防枚举 ✅）*/
export async function login(email: string, password: string): Promise<LoginResp> {
  const { data } = await http.post<LoginResp>('/api/auth/login', { email, password })
  return data
}

/**
 * `POST /api/auth/logout` —— **服务端撤销** ✅
 * 后端把这个 token 的 jti 写进 Redis 黑名单，TTL = 剩余有效期
 * → 哪怕有人截了这个 token，也立刻失效 ✅
 */
export async function logout(): Promise<{ ok: boolean; message?: string }> {
  const { data } = await http.post<{ ok: boolean; message?: string }>(
    '/api/auth/logout',
  )
  return data
}

/** `GET /api/auth/me` —— 刷新页面后用 token 换身份 */
export async function me(): Promise<LoginResp['user']> {
  const { data } = await http.get<LoginResp['user']>('/api/auth/me')
  return data
}
