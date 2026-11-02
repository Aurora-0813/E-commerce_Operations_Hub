// ══════════════════════════════════════════════════════════════
//  布局常量（唯一来源）—— 尺寸/颜色**禁止散落在组件里**
// ══════════════════════════════════════════════════════════════

/** 侧边栏宽度 */
export const SIDER_WIDTH = 240
/** 折叠后宽度（只放图标） */
export const SIDER_COLLAPSED_WIDTH = 64
/** 顶部导航高度 */
export const NAVBAR_HEIGHT = 56
/** 标签页高度 */
export const TABS_HEIGHT = 40
/** 低于这个宽度自动收起侧边栏（用户 prompt 要求 1200） */
export const AUTO_COLLAPSE_BREAKPOINT = 1200
/** 内容区最大宽度（超宽屏不要拉满，阅读更舒服 ✅） */
export const CONTENT_MAX_WIDTH = 1600

/** 间距（8px 栅格） */
export const SPACE = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const

/** 圆角 */
export const RADIUS = {
  sm: 4,
  base: 8, // 卡片统一 8px（用户 prompt 要求）
  lg: 12,
} as const

/** 阴影（用户 prompt 指定：0 2px 8px rgba(0,0,0,.06)） */
export const SHADOW = {
  card: '0 2px 8px rgba(0,0,0,.06)',
  hover: '0 4px 16px rgba(0,0,0,.10)',
} as const

/** 主色 */
export const BRAND = '#409eff'
/** 侧边栏深色（#1f2d3d 系） */
export const SIDER_BG = '#1f2d3d'
export const SIDER_BG_DARK = '#18222d'
export const SIDER_TEXT = '#bfcbd9'
export const SIDER_ACTIVE = '#409eff'

/** 过渡时长 */
export const DURATION = {
  fast: 150,
  base: 200,
  slow: 320,
} as const
