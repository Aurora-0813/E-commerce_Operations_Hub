<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  面包屑：**从路由 meta + menu.ts 派生**（不手写层级 ✅）
// ══════════════════════════════════════════════════════════════
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElBreadcrumb, ElBreadcrumbItem } from 'element-plus'

import { flattenMenu, findMenuByPath } from '@/config/menu'

const route = useRoute()

interface Crumb {
  title: string
  path: string
  /** 最后一级不可点 */
  linkable: boolean
}

const crumbs = computed<Crumb[]>(() => {
  const titleMap = flattenMenu()
  const trail = titleMap.get(route.path)

  // 菜单里有 → 用菜单层级（"处理 / Agent 任务"）
  if (trail) {
    const parts = trail.split(' / ')
    // 只有一级 → 直接返回
    if (parts.length === 1) {
      return [{ title: parts[0], path: route.path, linkable: false }]
    }
    // 多级 → 第一级不可点（它是分组，没有独立页面）
    return parts.map((t, i) => ({
      title: t,
      path: route.path,
      linkable: i === parts.length - 1 ? false : false,
    }))
  }

  // 详情页（/proposals/:id 等）→ 父级菜单 + 路由 meta.title
  const parent = findMenuByPath(route.path)
  const selfTitle = String(route.meta.title ?? '')
  if (parent && selfTitle) {
    return [
      { title: parent.title, path: parent.path, linkable: true },
      { title: selfTitle, path: route.path, linkable: false },
    ]
  }

  // 兜底：只显示 meta.title
  return selfTitle ? [{ title: selfTitle, path: route.path, linkable: false }] : []
})
</script>

<template>
  <ElBreadcrumb v-if="crumbs.length" class="breadcrumb" separator="/">
    <ElBreadcrumbItem v-for="(c, i) in crumbs" :key="`${c.path}-${i}`">
      <RouterLink v-if="c.linkable" :to="c.path" class="breadcrumb__link">
        {{ c.title }}
      </RouterLink>
      <span v-else class="breadcrumb__current">{{ c.title }}</span>
    </ElBreadcrumbItem>
  </ElBreadcrumb>
</template>

<style scoped lang="scss">
.breadcrumb {
  margin-bottom: $space-md;
  font-size: 12px;

  &__link {
    color: var(--hub-text-secondary);
    transition: color $duration-fast;

    &:hover {
      color: var(--el-color-primary);
    }
  }

  &__current {
    color: var(--hub-text-regular);
    font-weight: 500;
  }
}
</style>
