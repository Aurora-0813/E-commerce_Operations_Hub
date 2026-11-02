<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  侧边栏：Logo + 递归多级菜单 + 折叠（只显图标 + tooltip）
//  ⚠️ 菜单**从 config/menu.ts 来**（不是在这里写死 ✅）
//  ⚠️ 图标用 Element Plus 图标集（`@element-plus/icons-vue`）
// ══════════════════════════════════════════════════════════════
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElIcon, ElMenu, ElMenuItem, ElSubMenu } from 'element-plus'
import {
  Bell, Collection, DocumentChecked, MagicStick, Menu as MenuIcon, Monitor,
  Odometer, PieChart, Tools, TrendCharts, UploadFilled,
} from '@element-plus/icons-vue'

import { MENU } from '@/config/menu'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const route = useRoute()
const router = useRouter()

/** 图标表：`menu.ts` 里写的是字符串名 → 这里映射成组件 ✅ */
const ICONS: Record<string, unknown> = {
  Odometer, PieChart, Tools, DocumentChecked, MagicStick,
  TrendCharts, Bell, Collection, Monitor, UploadFilled, Menu: MenuIcon,
}

const collapsed = computed(() => app.collapsed)

/** 当前高亮的菜单（详情页要归属到父级 ✅）*/
const activePath = computed<string>(() => {
  const p = route.path
  // /proposals/:id → /proposals
  const hit = MENU.find((m: { path: string }) => p === m.path || p.startsWith(`${m.path}/`))
  if (hit) return hit.path
  // 二级里找
  for (const m of MENU) {
    const sub = m.children?.find((c: { path: string }) => p === c.path || p.startsWith(`${c.path}/`))
    if (sub) return sub.path
  }
  return p
})

function go(path: string): void {
  if (route.path !== path) void router.push(path)
}
</script>

<template>
  <div class="sidebar">
    <!-- Logo：折叠时只显 "电" -->
    <div class="sidebar__logo" @click="go('/dashboard')">
      <div class="sidebar__logo-mark">电</div>
      <Transition name="fade">
        <span v-if="!collapsed" class="sidebar__logo-text">运营决策中枢</span>
      </Transition>
    </div>

    <!-- 菜单 -->
    <ElMenu
      class="sidebar__menu"
      :default-active="activePath"
      :collapse="collapsed"
      :collapse-transition="false"
      background-color="transparent"
      text-color="#bfcbd9"
      active-text-color="#ffffff"
      @select="go"
    >
      <template v-for="item in MENU" :key="item.path">
        <!-- 有子菜单 -->
        <ElSubMenu v-if="item.children?.length" :index="item.path">
          <template #title>
            <ElIcon><component :is="ICONS[item.icon] ?? MenuIcon" /></ElIcon>
            <span>{{ item.title }}</span>
          </template>
          <ElMenuItem v-for="child in item.children" :key="child.path" :index="child.path">
            <ElIcon><component :is="ICONS[child.icon] ?? MenuIcon" /></ElIcon>
            <template #title>{{ child.title }}</template>
          </ElMenuItem>
        </ElSubMenu>

        <!-- 单级菜单 -->
        <!-- ⚠️ **不要在 ElMenuItem 里用 ElTooltip 包 ElIcon**（2026-11-01 修）
             症状：console 反复报 `Cannot destructure property 'type' of 'vnode' as it is null`
             原因：ElMenuItem 内部会重排子节点，Tooltip 的虚拟节点被丢弃 → vnode=null ✅
             修法：**用 ElMenuItem 自带的 `title` 属性**（折叠态自动出 tooltip ✅）-->
        <ElMenuItem v-else :index="item.path" :title="collapsed ? item.title : undefined">
          <ElIcon><component :is="ICONS[item.icon] ?? MenuIcon" /></ElIcon>
          <template #title>{{ item.title }}</template>
        </ElMenuItem>
      </template>
    </ElMenu>

    <!-- 底部：折叠后的版本号（很小，不抢眼）-->
    <div v-if="!collapsed" class="sidebar__footer">
      <span>v0.1 · 后端阶段 0~5</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--hub-sider-bg);

  // ---------- Logo ----------
  &__logo {
    display: flex;
    align-items: center;
    gap: $space-sm;
    height: $navbar-height;
    padding: 0 $space-md;
    flex: 0 0 auto;
    cursor: pointer;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    user-select: none;
  }

  &__logo-mark {
    flex: 0 0 auto;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    background: $brand;
  }

  &__logo-text {
    font-size: 14px;
    font-weight: 600;
    color: #e5eaf3;
    @include ellipsis;
  }

  // ---------- 菜单 ----------
  &__menu {
    flex: 1 1 auto;
    overflow-x: hidden;
    overflow-y: auto;
    border-right: none;
    padding: $space-sm 0;
    @include thin-scrollbar;

    // 菜单项圆角 + hover（Element 默认是直角满宽，视觉上比较钝）
    :deep(.el-menu-item),
    :deep(.el-sub-menu__title) {
      height: 42px;
      line-height: 42px;
      margin: 2px 8px;
      border-radius: $radius-sm;

      &:hover {
        background: rgba(255, 255, 255, 0.06) !important;
      }
    }

    // 选中态：左侧一条竖线 + 淡背景（比纯变色更清楚）
    :deep(.el-menu-item.is-active) {
      background: rgba(64, 158, 255, 0.16) !important;
      font-weight: 600;
      position: relative;

      &::before {
        content: '';
        position: absolute;
        left: 0;
        top: 8px;
        bottom: 8px;
        width: 3px;
        border-radius: 0 3px 3px 0;
        background: $brand;
      }
    }

    // 折叠后：图标居中
    &:deep(.el-menu--collapse) .el-menu-item,
    &:deep(.el-menu--collapse) .el-sub-menu__title {
      margin: 2px 6px;
      justify-content: center;
    }
  }

  // ---------- 底部 ----------
  &__footer {
    flex: 0 0 auto;
    padding: $space-sm $space-md;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.28);
    border-top: 1px solid rgba(255, 255, 255, 0.06);
  }
}

// 折叠时不显示 logo 文字（Transition 的离开动画）
.fade-leave-active {
  transition: opacity 120ms;
}
</style>
