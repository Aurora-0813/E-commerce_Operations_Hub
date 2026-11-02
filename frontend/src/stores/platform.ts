// ══════════════════════════════════════════════════════════════
//  平台上下文 store —— 全局「平台 + 店铺」切换器
//  ⚠️ 切了之后**所有页面感知**（今日工作台 / 问题全景 / 复盘都按这个过滤 ✅）
// ══════════════════════════════════════════════════════════════
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { PLATFORM_LIST, platformMeta, type PlatformKey } from '@/config/platform'

const KEY_PLATFORM = 'hub_platform'

export interface ShopOption {
  /** 后端目前没有"店铺"维度 ✅ —— **先留接口位，值为 null 表示"全部店铺"** ⚠️
   *  为什么先不做：`docs/数据模型.md` 里没有 shop 表；
   *  真加的时候要动 5 张表，不是前端能决定的 ✅
   */
  id: string
  name: string
}

export const usePlatformStore = defineStore('platform', () => {
  const raw = localStorage.getItem(KEY_PLATFORM)
  const platform = ref<PlatformKey>(
    raw === 'taobao' || raw === 'jd' ? raw : 'taobao',
  )
  /** 店铺：后端暂无该维度 → 恒为 null（"全部店铺"）✅ */
  const shop = ref<ShopOption | null>(null)

  const meta = computed(() => platformMeta(platform.value))
  /** 传给后端接口的参数（**空数组 = 不过滤** ✅）*/
  const platformsParam = computed<string[]>(() => [platform.value])
  /** 多平台徽标用 */
  const all = PLATFORM_LIST

  function setPlatform(v: PlatformKey): void {
    platform.value = v
  }
  function toggle(): void {
    platform.value = platform.value === 'taobao' ? 'jd' : 'taobao'
  }
  watch(platform, (v) => localStorage.setItem(KEY_PLATFORM, v))

  return { platform, shop, meta, all, platformsParam, setPlatform, toggle }
})
