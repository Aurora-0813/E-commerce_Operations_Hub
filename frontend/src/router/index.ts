// ══════════════════════════════════════════════════════════════
//  路由（**唯一来源**：菜单从这里的 meta 派生 ✅）
//  ⚠️ 守卫逻辑：未登录 → /login；已登录且 token 过期 → 拉一次 /auth/me
// ══════════════════════════════════════════════════════════════
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useUserStore } from '@/stores/user'

/** 页面路由（**children 挂在布局下** ✅）*/
export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { hidden: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/default/index.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '今日工作台', icon: 'Odometer' },
      },
      {
        path: 'ingest',
        name: 'Ingest',
        component: () => import('@/views/ingest/index.vue'),
        meta: { title: '数据接入', icon: 'UploadFilled' },
      },
      {
        path: 'problems',
        name: 'Problems',
        component: () => import('@/views/problems/index.vue'),
        meta: { title: '问题全景', icon: 'PieChart' },
      },
      {
        path: 'proposals',
        name: 'Proposals',
        component: () => import('@/views/proposals/index.vue'),
        meta: { title: '待批提案', icon: 'DocumentChecked' },
      },
      {
        path: 'proposals/:id',
        name: 'ProposalDetail',
        component: () => import('@/views/proposals/detail.vue'),
        // **详情页不在菜单里**，但面包屑要能显示
        meta: { title: '提案详情', hidden: true },
      },
      {
        path: 'agent',
        name: 'Agent',
        component: () => import('@/views/agent/index.vue'),
        meta: { title: 'Agent 任务', icon: 'MagicStick' },
      },
      {
        path: 'retro',
        name: 'Retro',
        component: () => import('@/views/retro/index.vue'),
        meta: { title: '复盘报告', icon: 'TrendCharts' },
      },
      {
        path: 'notify',
        name: 'Notify',
        component: () => import('@/views/notify/index.vue'),
        meta: { title: '通知中心', icon: 'Bell' },
      },
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('@/views/knowledge/index.vue'),
        meta: { title: '知识库', icon: 'Collection' },
      },
      {
        path: 'ops',
        name: 'Ops',
        component: () => import('@/views/ops/index.vue'),
        meta: { title: '运维台', icon: 'Monitor' },
      },
    ],
  },
  // 兜底：不认识的路径回工作台（**不要白屏** ✅）
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ══════════════════ 守卫 ══════════════════
router.beforeEach(async (to) => {
  const user = useUserStore()

  // ① 去登录页：已登录就送回工作台
  if (to.path === '/login') {
    return user.isLoggedIn ? { path: '/dashboard' } : true
  }

  // ② 未登录：跳登录，并记住原来想去的地址（登录后跳回来 ✅）
  if (!user.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // ③ 已登录但内存里没有用户（刷新页面）：用 token 换一次身份
  //    ⚠️ 这里**不 try/catch 吞掉异常**：401 会由 client.ts 清 token 并跳登录 ✅
  if (!user.user) {
    try {
      await user.fetchMe()
    } catch {
      user.forceClear()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }

  return true
})

export default router
