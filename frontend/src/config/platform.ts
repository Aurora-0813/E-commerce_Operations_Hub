// ══════════════════════════════════════════════════════════════
//  平台配色（**唯一来源**）—— 同一平台全局同色，禁止在组件里写死
// ══════════════════════════════════════════════════════════════

/** 后端只认这两个值（`hub/ingress/adapter.py` 的白名单 ✅） */
export type PlatformKey = 'taobao' | 'jd'

export interface PlatformMeta {
  key: PlatformKey
  label: string
  /** 主色（徽标/标签/图表系列用） */
  color: string
  /** 浅色底（表格行/标签背景） */
  bg: string
  /** 边框 */
  border: string
}

export const PLATFORMS: Record<PlatformKey, PlatformMeta> = {
  taobao: {
    key: 'taobao',
    label: '淘宝',
    color: '#ff5000',
    bg: 'rgba(255, 80, 0, 0.08)',
    border: 'rgba(255, 80, 0, 0.28)',
  },
  jd: {
    key: 'jd',
    label: '京东',
    color: '#e1251b',
    bg: 'rgba(225, 37, 27, 0.08)',
    border: 'rgba(225, 37, 27, 0.28)',
  },
}

/** 列表渲染用（顺序固定：淘宝在前） */
export const PLATFORM_LIST: PlatformMeta[] = [PLATFORMS.taobao, PLATFORMS.jd]

/** 按 key 取；未知 → 灰色兜底（**不要抛错**，后端加了新平台时前端不该白屏 ✅） */
export function platformMeta(key: string | null | undefined): PlatformMeta {
  if (key === 'taobao' || key === 'jd') return PLATFORMS[key]
  return { key: 'taobao', label: key || '未知', color: '#909399',
           bg: 'rgba(144,147,153,.08)', border: 'rgba(144,147,153,.28)' }
}

/** 多平台徽标（一个商品/簇可能跨两个平台）*/
export function platformLabels(keys: string[] | null | undefined): string {
  if (!keys || keys.length === 0) return '—'
  return keys.map((k) => platformMeta(k).label).join(' / ')
}
