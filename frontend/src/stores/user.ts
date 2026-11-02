// ══════════════════════════════════════════════════════════════
//  用户 store —— token / 身份 / 角色
//  ⚠️ token 存在 localStorage（3 小时会过期 ✅，401 时 axios 会清掉并跳登录）
// ══════════════════════════════════════════════════════════════
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { clearToken, getToken, setToken } from '@/api/client'
import { login as apiLogin, logout as apiLogout, me as apiMe } from '@/api/auth'

export type Role = 'analyst' | 'operator' | 'supervisor' | 'admin'

export interface CurrentUser {
  id: string
  email: string
  display_name: string
  role: Role
}

const USER_KEY = 'hub_user'

function readCachedUser(): CurrentUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as CurrentUser
  } catch {
    return null
  }
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(getToken())
  const user = ref<CurrentUser | null>(readCachedUser())

  const isLoggedIn = computed(() => Boolean(token.value))
  const role = computed<Role>(() => user.value?.role ?? 'analyst')
  const displayName = computed(() => user.value?.display_name ?? '')

  /** 角色够不够（**和文档里同一套顺序** ✅ system < analyst < operator < supervisor < admin）*/
  const ROLE_ORDER: Record<Role, number> = {
    analyst: 1, operator: 2, supervisor: 3, admin: 4,
  }
  function hasRole(min: Role): boolean {
    const mine = ROLE_ORDER[role.value] ?? 0
    return mine >= (ROLE_ORDER[min] ?? 99)
  }

  function persist(u: CurrentUser): void {
    user.value = u
    localStorage.setItem(USER_KEY, JSON.stringify(u))
  }

  async function login(email: string, password: string): Promise<void> {
    const data = await apiLogin(email, password)
    setToken(data.access_token)
    token.value = data.access_token
    persist(data.user as CurrentUser)
  }

  /** 刷新页面后用 token 换一次身份（**token 有效但 localStorage 被清了也能恢复** ✅）*/
  async function fetchMe(): Promise<void> {
    if (!token.value) return
    const u = await apiMe()
    persist(u as CurrentUser)
  }

  /**
   * 登出 —— **必须调后端** ✅
   * ⚠️ 不能只清本地 token：后端有 Redis 黑名单，不调接口的话
   *    这个 token 在 3 小时内**仍然有效** ✅ ✗
   */
  async function logout(): Promise<void> {
    try {
      await apiLogout()
    } catch {
      // 后端 503（Redis 挂了）也要把本地清掉 —— 否则用户以为已登出但 token 还在
    } finally {
      clearToken()
      token.value = ''
      user.value = null
    }
  }

  /** axios 收到 401 时由 main.ts 调这里（**清干净，然后 router 跳登录** ✅）*/
  function forceClear(): void {
    clearToken()
    token.value = ''
    user.value = null
  }

  return {
    token, user, isLoggedIn, role, displayName,
    hasRole, login, fetchMe, logout, forceClear,
  }
})
