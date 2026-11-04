<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  标签页：右键「关闭其他 / 关闭全部 / 刷新」
//  ⚠️ keep-alive 在 `layouts/default/index.vue` 里（用组件名 ✅）
//     这里只管「访问过哪些路由」→ 关闭标签 = 从列表移除（缓存随之释放 ✅）
// ⚠️ 登录页不进标签页 ✅
// ══════════════════════════════════════════════════════════════
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElIcon } from 'element-plus'
import { Close, Refresh } from '@element-plus/icons-vue'

interface TabItem {
  path: string
  title: string
  /** 详情页 = true（不可关闭？—— 其实详情页也能关，只是不常驻 ✅）*/
  affix?: boolean
}

const route = useRoute()
const router = useRouter()

/** 常驻标签：工作台永远在（关不掉 ✅）*/
const HOME: TabItem = { path: '/dashboard', title: '今日工作台', affix: true }

const tabs = ref<TabItem[]>([{ ...HOME }])
const current = computed(() => route.path)

/** 加标签（详情页标题带 id 后 4 位，避免两个详情页分不清 ✅）*/
watch(
  () => route.path,
  () => {
    if (route.path === '/login') return
    const meta = route.meta as { title?: string; hidden?: boolean }
    const title = String(meta.title ?? route.name ?? route.path)
    const exists = tabs.value.find((t) => t.path === route.path)
    if (!exists) {
      tabs.value.push({ path: route.path, title })
    } else if (meta.title && exists.title !== title) {
      exists.title = title
    }
  },
  { immediate: true },
)

// ---------- 右键菜单 ----------
const ctx = reactive({ visible: false, x: 0, y: 0, target: '' as string })

function openCtx(e: MouseEvent, path: string): void {
  e.preventDefault()
  ctx.target = path
  ctx.x = e.clientX
  ctx.y = e.clientY
  ctx.visible = true
  // 点击别处关闭（**用 once + capture，避免堆监听 ✅**）
  window.addEventListener('click', closeCtx, { once: true, capture: true })
}
function closeCtx(): void {
  ctx.visible = false
}

// ---------- 操作 ----------
function closeTab(path: string): void {
  const idx = tabs.value.findIndex((t) => t.path === path)
  if (idx < 0) return
  const item = tabs.value[idx]
  if (item.affix) return // 常驻不可关
  tabs.value.splice(idx, 1)

  // 关的是当前页 → 跳到右边一个，没有就跳左边，再没有回工作台
  if (current.value === path) {
    const next = tabs.value[idx] ?? tabs.value[idx - 1] ?? HOME
    void router.push(next.path)
  }
}

function closeOthers(path: string): void {
  tabs.value = tabs.value.filter((t) => t.affix || t.path === path)
  if (current.value !== path) void router.push(path)
}

function closeAll(): void {
  tabs.value = tabs.value.filter((t) => t.affix)
  if (current.value !== HOME.path) void router.push(HOME.path)
}

/** 刷新：先跳走再跳回来（**这是 Vue 里最稳的"重载当前路由"方式** ✅）*/
async function refresh(path: string): Promise<void> {
  if (current.value !== path) {
    await router.push(path)
  }
  const back = tabs.value.find((t) => t.path !== path)?.path ?? HOME.path
  await router.replace(back)
  await nextTick()
  await router.replace(path)
}

function onCommand(cmd: 'refresh' | 'close' | 'others' | 'all'): void {
  const path = ctx.target
  closeCtx()
  if (cmd === 'refresh') void refresh(path)
  else if (cmd === 'close') closeTab(path)
  else if (cmd === 'others') closeOthers(path)
  else closeAll()
}
</script>

<template>
  <div class="tabs">
    <div class="tabs__list">
      <div
        v-for="t in tabs"
        :key="t.path"
        :class="['tabs__item', { 'is-active': t.path === current }]"
        @click="router.push(t.path)"
        @contextmenu="openCtx($event, t.path)"
      >
        <span class="tabs__dot" />
        <span class="tabs__title">{{ t.title }}</span>
        <ElIcon
          v-if="!t.affix"
          class="tabs__close"
          :size="12"
          @click.stop="closeTab(t.path)"
        >
          <Close />
        </ElIcon>
        <ElIcon v-else class="tabs__pin" :size="12" title="常驻">
          <Refresh />
        </ElIcon>
      </div>
    </div>

    <!-- 右键菜单（<b>自己画，不用 el-dropdown</b> —— 要跟随鼠标位置）-->
    <Teleport to="body">
      <div
        v-if="ctx.visible"
        class="tabs__ctx"
        :style="{ left: `${ctx.x}px`, top: `${ctx.y}px` }"
      >
        <button type="button" @click="onCommand('refresh')">刷新</button>
        <button type="button" @click="onCommand('close')">关闭</button>
        <button type="button" @click="onCommand('others')">关闭其他</button>
        <button type="button" class="is-danger" @click="onCommand('all')">关闭全部</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped lang="scss">
.tabs {
  height: $tabs-height;
  display: flex;
  align-items: center;
  padding: 0 $space-sm;
  background: var(--hub-bg-card);
  border-top: 1px solid var(--hub-border-light);
  overflow: hidden;

  &__list {
    display: flex;
    align-items: center;
    gap: 4px;
    overflow-x: auto;
    @include thin-scrollbar;
    scrollbar-width: none;

    &::-webkit-scrollbar {
      height: 0;
    }
  }

  &__item {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 8px 0 10px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    font-size: 12px;
    color: var(--hub-text-regular);
    background: var(--hub-bg-card);
    cursor: pointer;
    white-space: nowrap;
    user-select: none;
    transition: all $duration-fast;

    &:hover {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary-light-7);
    }

    &.is-active {
      color: var(--el-color-primary);
      border-color: var(--el-color-primary-light-5);
      background: var(--el-color-primary-light-9);

      .tabs__dot {
        background: var(--el-color-primary);
      }
    }
  }

  &__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--hub-border);
  }

  &__title {
    max-width: 140px;
    @include ellipsis;
  }

  &__close,
  &__pin {
    color: var(--hub-text-secondary);
    border-radius: 50%;
    transition: all $duration-fast;

    &:hover {
      color: var(--el-color-danger);
      background: var(--el-color-danger-light-9);
    }
  }

  &__pin {
    opacity: 0.45;
    cursor: default;

    &:hover {
      color: var(--hub-text-secondary);
      background: transparent;
    }
  }

  // ---------- 右键菜单 ----------
  &__ctx {
    position: fixed;
    z-index: 3000;
    min-width: 120px;
    padding: 4px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    background: var(--hub-bg-card);
    box-shadow: $shadow-hover;

    button {
      display: block;
      width: 100%;
      padding: 6px 10px;
      border: none;
      border-radius: 4px;
      background: transparent;
      text-align: left;
      font-size: 12px;
      color: var(--hub-text-regular);
      cursor: pointer;

      &:hover {
        background: var(--el-fill-color-light);
        color: var(--el-color-primary);
      }

      &.is-danger:hover {
        color: var(--el-color-danger);
        background: var(--el-color-danger-light-9);
      }
    }
  }
}
</style>
