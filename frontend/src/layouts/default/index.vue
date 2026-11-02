<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  主框架：侧边栏 + 导航 + 标签页 + 内容区
//  ⚠️ 布局**不放在 App.vue** 里 —— 登录页不要布局 ✅
//  ⚠️ 侧边栏宽度在两个值之间切换（240 / 64），用 CSS 变量驱动
// ══════════════════════════════════════════════════════════════
import { computed, onBeforeUnmount, onMounted } from 'vue'

import AppBreadcrumb from '@/layouts/components/Breadcrumb/index.vue'
import AppNavbar from '@/layouts/components/Navbar/index.vue'
import AppSidebar from '@/layouts/components/Sidebar/index.vue'
import AppTabs from '@/layouts/components/Tabs/index.vue'
import { AUTO_COLLAPSE_BREAKPOINT, SIDER_COLLAPSED_WIDTH, SIDER_WIDTH } from '@/config/layout'
import { useAppStore } from '@/stores/app'

const app = useAppStore()

const siderWidth = computed(() => `${app.collapsed ? SIDER_COLLAPSED_WIDTH : SIDER_WIDTH}px`)

// ---------- 窄屏自动收起（<1200px）----------
//   ⚠️ 用 `matchMedia` 而不是 window.resize：只在**跨越断点**时触发，不是每帧都算 ✅
//   ⚠️ 卸载必须 removeEventListener（用户 prompt 硬要求 ✅）
let mql: MediaQueryList | null = null
let onBreach: ((e: MediaQueryListEvent) => void) | null = null
/** 记住"是由窄屏自动收起的"，回到宽屏时才自动展开（**不覆盖用户的主动选择** ✅）*/
let autoCollapsed = false

onMounted(() => {
  mql = window.matchMedia(`(max-width: ${AUTO_COLLAPSE_BREAKPOINT}px)`)

  const apply = (narrow: boolean): void => {
    if (narrow) {
      if (!app.collapsed) {
        app.setCollapsed(true)
        autoCollapsed = true
      }
    } else if (autoCollapsed) {
      app.setCollapsed(false)
      autoCollapsed = false
    }
  }

  onBreach = (e: MediaQueryListEvent) => apply(e.matches)
  mql.addEventListener('change', onBreach)
  // 首次进入也要判断一次（用户可能直接开在 1100px 宽）
  apply(mql.matches)
})

onBeforeUnmount(() => {
  if (mql && onBreach) mql.removeEventListener('change', onBreach)
  mql = null
  onBreach = null
})
</script>

<template>
  <div class="layout" :style="{ '--sider-w': siderWidth }">
    <!-- 侧边栏 -->
    <aside class="layout__aside">
      <AppSidebar />
    </aside>

    <!-- 右侧 -->
    <div class="layout__main">
      <header class="layout__header">
        <AppNavbar />
        <AppTabs />
      </header>

      <main class="layout__content">
        <div class="layout__inner">
          <AppBreadcrumb />
          <RouterView v-slot="{ Component, route }">
            <!-- keep-alive 用<b>组件名</b>（路由名会和详情页冲突 ✅）-->
            <Transition name="fade" mode="out-in">
              <KeepAlive :max="8">
                <component :is="Component" :key="route.path" />
              </KeepAlive>
            </Transition>
          </RouterView>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped lang="scss">
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--hub-bg-page);

  // ---------- 侧边栏 ----------
  &__aside {
    flex: 0 0 var(--sider-w);
    width: var(--sider-w);
    height: 100%;
    overflow: hidden;
    // 折叠动画 200ms（用户 prompt 要求）
    transition: width $duration-base ease, flex-basis $duration-base ease;
    background: var(--hub-sider-bg);
  }

  // ---------- 右侧 ----------
  &__main {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  &__header {
    flex: 0 0 auto;
    background: var(--hub-bg-card);
    border-bottom: 1px solid var(--hub-border-light);
    // 头部固定（内容区自己滚）
    z-index: 10;
  }

  &__content {
    flex: 1 1 auto;
    min-height: 0;
    overflow: auto;
    @include thin-scrollbar;
  }

  &__inner {
    max-width: 1600px;
    margin: 0 auto;
    padding: $space-lg;
  }

  // 窄屏：内容区 padding 小一点
  @media (max-width: 860px) {
    &__inner {
      padding: $space-md;
    }
  }
}
</style>
