// ══════════════════════════════════════════════════════════════
//  菜单配置（**从路由派生，单一来源**）
//  ⚠️ 不要另写一份"菜单列表"——否则加页面要改两处，必然会忘一处 ✅
// ══════════════════════════════════════════════════════════════
import type { RouteRecordRaw } from 'vue-router'

export interface MenuItem {
  /** 路由 path（**唯一键**） */
  path: string
  /** 侧边栏显示的名字（中文） */
  title: string
  /** Element Plus 图标组件名（`ElIconXxx`，见 `src/components/MenuIcon.vue`） */
  icon: string
  /** 子菜单（多级） */
  children?: MenuItem[]
  /** 不在菜单里显示（如详情页 / 登录页） */
  hidden?: boolean
  /** 需要的最低角色（用于"operator 看不到批准"这类 UI 级隐藏 ✅） */
  minRole?: 'analyst' | 'operator' | 'supervisor' | 'admin'
}

/**
 * 菜单树（**顺序 = 用户上班的动线** ✅）
 *
 * 为什么顺序是这样：
 *   今日工作台 —— 每天第一眼
 *   问题全景   —— 有问题再往下钻
 *   提案详情 / Agent 任务 —— 处理
 *   复盘报告   —— 处理完看效果
 *   通知中心   —— 有人找我
 *   知识库     —— 沉淀口径
 *   运维台     —— 工程视角（放最后，运营不常看）
 */
export const MENU: MenuItem[] = [
  { path: '/dashboard', title: '今日工作台', icon: 'Odometer' },
  { path: '/problems', title: '问题全景', icon: 'PieChart' },
  // **数据接入放这里**：运营的日常操作（每周导一次），不该藏在运维台 ✅
  { path: '/ingest', title: '数据接入', icon: 'UploadFilled', minRole: 'operator' },
  {
    path: '/work',
    title: '处理',
    icon: 'Tools',
    children: [
      { path: '/proposals', title: '待批提案', icon: 'DocumentChecked' },
      { path: '/agent', title: 'Agent 任务', icon: 'MagicStick' },
  { path: '/images', title: '图片复核', icon: 'PictureFilled' },
    ],
  },
  { path: '/retro', title: '复盘报告', icon: 'TrendCharts' },
  { path: '/notify', title: '通知中心', icon: 'Bell' },
  { path: '/knowledge', title: '知识库', icon: 'Collection' },
  { path: '/ops', title: '运维台', icon: 'Monitor', minRole: 'operator' },
]

/** 面包屑用：path → 名称（从菜单拍平，含子级） */
export function flattenMenu(items: MenuItem[] = MENU): Map<string, string> {
  const map = new Map<string, string>()
  const walk = (list: MenuItem[], trail: string[]): void => {
    for (const it of list) {
      const names = [...trail, it.title]
      map.set(it.path, names.join(' / '))
      if (it.children) walk(it.children, names)
    }
  }
  walk(items, [])
  return map
}

/** 路由 path → 菜单项（判断"该不该高亮"用）*/
export function findMenuByPath(path: string, items: MenuItem[] = MENU): MenuItem | null {
  for (const it of items) {
    if (path === it.path || path.startsWith(`${it.path}/`)) return it
    if (it.children) {
      const hit = findMenuByPath(path, it.children)
      if (hit) return hit
    }
  }
  return null
}

/** 从路由表反推菜单（备用：如果以后想让路由带 `meta.menu` 自动生成） */
export function menuFromRoutes(routes: RouteRecordRaw[]): MenuItem[] {
  const out: MenuItem[] = []
  for (const r of routes) {
    const meta = (r.meta || {}) as Record<string, unknown>
    if (meta.hidden === true) continue
    if (typeof meta.title !== 'string') continue
    out.push({
      path: `/${String(r.path).replace(/^\//, '')}`,
      title: String(meta.title),
      icon: typeof meta.icon === 'string' ? meta.icon : 'Menu',
      hidden: false,
    })
  }
  return out
}

// ══════════════════════════════════════════════════════════════
//  按角色的两套菜单（2026-11-03 重写）
// ══════════════════════════════════════════════════════════════
// **上一版的问题**（用户反馈）
//   菜单名太口语化：「数据到了没」「我来处理」「系统找我」✅ ✗
//   导航栏有「视角切换」按钮 → **不该有** ✅
//      应该**登录哪个角色就用哪套菜单**（权限决定 ✅）
//
// **命名原则**：专业、简短、名词化（不写句子 ✅）
//   ✗ 今天要做什么   → ✓ 工作台
//   ✗ 数据到了没     → ✓ 数据接入
//   ✗ 有什么问题     → ✓ 问题分析
//   ✗ 我来处理       → ✓ 处置中心
//   ✗ 系统找我       → ✓ 通知中心
//   ✗ 看效果         → ✓ 复盘分析
//   ✗ 待我审批       → ✓ 审批中心
//   ✗ 治理           → ✓ 管理
export const MENU_OPERATOR: MenuItem[] = [
  { path: '/dashboard', title: '工作台', icon: 'Odometer' },
  { path: '/ingest', title: '数据接入', icon: 'UploadFilled', minRole: 'operator' },
  { path: '/problems', title: '问题分析', icon: 'PieChart' },
  {
    path: '/work',
    title: '处置中心',
    icon: 'DocumentChecked',
    children: [
      { path: '/proposals', title: '处置提案', icon: 'DocumentChecked' },
      { path: '/images', title: '图片核验', icon: 'PictureFilled' },
    ],
  },
  { path: '/retro', title: '复盘分析', icon: 'TrendCharts' },
  { path: '/more', title: '其他', icon: 'Menu', children: [
    { path: '/notify', title: '通知中心', icon: 'Bell' },
    { path: '/knowledge', title: '知识库', icon: 'Collection' },
    { path: '/ops', title: '系统日志', icon: 'Monitor', minRole: 'operator' },
  ] },
]

export const MENU_SUPERVISOR: MenuItem[] = [
  { path: '/proposals', title: '审批中心', icon: 'DocumentChecked' },
  { path: '/retro', title: '复盘分析', icon: 'TrendCharts' },
  { path: '/problems', title: '问题分析', icon: 'PieChart' },
  { path: '/dashboard', title: '工作台', icon: 'Odometer' },
  {
    path: '/manage',
    title: '管理',
    icon: 'Monitor',
    children: [
      { path: '/ops', title: '审计日志', icon: 'Monitor', minRole: 'operator' },
      { path: '/knowledge', title: '知识库', icon: 'Collection' },
      { path: '/images', title: '图片核验', icon: 'PictureFilled' },
      { path: '/ingest', title: '数据接入', icon: 'UploadFilled', minRole: 'operator' },
      { path: '/notify', title: '通知中心', icon: 'Bell' },
    ],
  },
]

/**
 * **按登录角色取菜单**（不再有"视角切换"按钮 ✅）
 *
 * 角色序：`system < analyst < operator < supervisor < admin`
 * 主管 / 管理员 → 主管菜单（审批中心为主入口 ✅）
 * 其他         → 运营菜单（工作台为主入口 ✅）
 */
export function menuForRole(role: string | null | undefined): MenuItem[] {
  const r = String(role ?? '').toLowerCase()
  return (r === 'supervisor' || r === 'admin') ? MENU_SUPERVISOR : MENU_OPERATOR
}
