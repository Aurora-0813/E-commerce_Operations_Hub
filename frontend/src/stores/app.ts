// ══════════════════════════════════════════════════════════════
//  应用设置 store —— 侧边栏折叠 / 暗色主题 / 时间范围
//  ⚠️ 全部落 localStorage（刷新后保持 ✅）
// ══════════════════════════════════════════════════════════════
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export type RangeKey = 'today' | '7d' | '30d' | 'custom'

const KEY_COLLAPSED = 'hub_sider_collapsed'
const KEY_DARK = 'hub_dark'
const KEY_RANGE = 'hub_range'

function readBool(key: string, fallback = false): boolean {
  const v = localStorage.getItem(key)
  if (v === null) return fallback
  return v === '1'
}

export const useAppStore = defineStore('app', () => {
  /** 侧边栏折叠 */
  const collapsed = ref<boolean>(readBool(KEY_COLLAPSED))
  /** 暗色主题 */
  const dark = ref<boolean>(readBool(KEY_DARK))
  /** 时间范围（顶部那三个按钮 + 自定义）*/
  const range = ref<RangeKey>(
    (localStorage.getItem(KEY_RANGE) as RangeKey | null) ?? '7d',
  )
  /** 自定义范围（range === 'custom' 时有效）*/
  const customRange = ref<[string, string] | null>(null)

  /** 内容区最大宽度（超宽屏不拉满）*/
  const contentMaxWidth = 1600

  // ---------- 侧边栏 ----------
  function toggleCollapsed(): void {
    collapsed.value = !collapsed.value
  }
  function setCollapsed(v: boolean): void {
    collapsed.value = v
  }
  watch(collapsed, (v) => localStorage.setItem(KEY_COLLAPSED, v ? '1' : '0'))

  // ---------- 主题 ----------
  /** 应用暗色：**Element Plus 和 ECharts 都要跟着变** ✅ */
  function applyDark(v: boolean): void {
    dark.value = v
    const root = document.documentElement
    root.classList.toggle('dark', v)
    // Element Plus 暗色走 `html.dark` + css-vars ✅
    // 我们自己定义的 CSS 变量（theme.scss）也靠 `.dark` 切换 ✅
  }
  function toggleDark(): void {
    applyDark(!dark.value)
  }
  watch(dark, (v) => {
    localStorage.setItem(KEY_DARK, v ? '1' : '0')
    applyDark(v)
  })

  // ---------- 时间范围 ----------
  function setRange(v: RangeKey): void {
    range.value = v
    localStorage.setItem(KEY_RANGE, v)
  }
  function setCustomRange(v: [string, string] | null): void {
    customRange.value = v
    if (v) setRange('custom')
  }
  /** 把 range 翻译成后端接口吃的 `days` 数字 */
  const rangeDays = computed<number>(() => {
    if (range.value === 'today') return 1
    if (range.value === '7d') return 7
    if (range.value === '30d') return 30
    return 7
  })

  /** 首次挂载时应用一次（main.ts 调用）*/
  function init(): void {
    applyDark(dark.value)
  }

  return {
    collapsed, dark, range, customRange, contentMaxWidth, rangeDays,
    toggleCollapsed, setCollapsed,
    toggleDark, applyDark, init,
    setRange, setCustomRange,
  }
})
