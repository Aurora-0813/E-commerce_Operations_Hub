
<script setup lang="ts">
// ═══════════════════════════════════════════════════
//  顶部导航：折叠 / 平台 / 时间范围 / 通知 / 暗色 / 全屏 / 用户
//  ⚠️ 通知未读数来自 `GET /api/notify` 的 `unread` ✅
//  ⚠️ 登出**必须调后端**（服务端撤销 ✅），不能只清本地 token
// ══════════════════════════════════════════════════════════════
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElBadge, ElButton, ElDropdown, ElDropdownItem, ElDropdownMenu, ElIcon,
  ElMessage, ElTooltip,
} from 'element-plus'
import {
  Bell, Expand, Fold, FullScreen, Moon, Sunny, User,
} from '@element-plus/icons-vue'

import { getNotify } from '@/api/read'
import { useAppStore, type RangeKey } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import PlatformSwitcher from './PlatformSwitcher.vue'

const app = useAppStore()
const user = useUserStore()
const router = useRouter()

/** 未读数（铃铛上的数字 ✅）*/
const unread = ref(0)
/** 全屏状态 */
const isFullscreen = ref(false)

// ---------- 通知轮询 ----------
//   ⚠️ 60 秒够了：通知不是实时业务（后端靠 cron 每 5 分钟 scan ✅）
let timer: number | null = null
async function pullUnread(): Promise<void> {
  try {
    const d = await getNotify(1)
    unread.value = d.unread
  } catch {
    // 通知拉不到不影响主流程 —— **静默**（不要每 60 秒弹一次错 ✅）
  }
}

onMounted(() => {
  void pullUnread()
  timer = window.setInterval(() => void pullUnread(), 60_000)
  document.addEventListener('fullscreenchange', syncFullscreen)
})

onBeforeUnmount(() => {
  // ⚠️ 必须清：否则离开布局后还在每 60 秒打一次接口 ✅
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
  document.removeEventListener('fullscreenchange', syncFullscreen)
})

// ---------- 全屏 ----------
function syncFullscreen(): void {
  isFullscreen.value = Boolean(document.fullscreenElement)
}
async function toggleFullscreen(): Promise<void> {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen()
  } catch {
    ElMessage.warning('浏览器不允许全屏（可能是权限或快捷键限制）')
  }
}

// ---------- 角色 / 用户 ----------
const roleLabel = computed(() => {
  const map: Record<string, string> = {
    analyst: '分析员', operator: '运营', supervisor: '主管', admin: '管理员',
  }
  return map[user.role] ?? user.role
})

async function onLogout(): Promise<void> {
  await user.logout()
  ElMessage.success('已登出（这个 token 立刻失效）')
  await router.replace('/login')
}

/** el-radio-group 给的是 string|number|boolean|undefined —— 收窄成 RangeKey ✅ */
function onRangeChange(v: string | number | boolean | undefined): void {
  if (v === 'today' || v === '7d' || v === '30d') {
    app.setRange(v as RangeKey)
  }
}

function goNotify(): void {
  void router.push('/notify')
}
</script>

<template>
  <div class="navbar">
    <!-- 左：折叠 + 平台 -->
    <div class="navbar__left">
      <ElButton text class="navbar__icon-btn" @click="app.toggleCollapsed">
        <ElIcon :size="18">
          <component :is="app.collapsed ? Expand : Fold" />
        </ElIcon>
      </ElButton>

      <PlatformSwitcher />
    </div>

    <!-- 右：范围 / 通知 / 暗色 / 全屏 / 用户 -->
    <div class="navbar__right">
      <!-- 时间范围（写在 app store，页面自己取 rangeDays）-->
      <el-radio-group :model-value="app.range" size="small"
                      @update:model-value="onRangeChange">
        <el-radio-button value="today">今天</el-radio-button>
        <el-radio-button value="7d">近 7 天</el-radio-button>
        <el-radio-button value="30d">近 30 天</el-radio-button>
      </el-radio-group>

      <!-- 通知 -->
      <ElTooltip :content="unread > 0 ? `${unread} 条未读` : '没有未读通知'" placement="bottom">
        <ElButton text class="navbar__icon-btn" @click="goNotify">
          <ElBadge :value="unread" :hidden="unread === 0" :max="99">
            <ElIcon :size="18"><Bell /></ElIcon>
          </ElBadge>
        </ElButton>
      </ElTooltip>

      <!-- 暗色 -->
      <ElTooltip :content="app.dark ? '切到浅色' : '切到暗色'" placement="bottom">
        <ElButton text class="navbar__icon-btn" @click="app.toggleDark">
          <ElIcon :size="18"><component :is="app.dark ? Sunny : Moon" /></ElIcon>
        </ElButton>
      </ElTooltip>

      <!-- 全屏 -->
      <ElTooltip content="全屏" placement="bottom">
        <ElButton text class="navbar__icon-btn" @click="toggleFullscreen">
          <ElIcon :size="18"><FullScreen /></ElIcon>
        </ElButton>
      </ElTooltip>

      <!-- 用户 -->
      <ElDropdown trigger="click" @command="(c: string) => c === 'logout' ? onLogout() : undefined">
        <div class="navbar__user">
          <ElIcon :size="16"><User /></ElIcon>
          <span class="navbar__user-name">{{ user.displayName || user.user?.email }}</span>
          <span class="navbar__user-role">{{ roleLabel }}</span>
        </div>
        <template #dropdown>
          <ElDropdownMenu>
            <ElDropdownItem disabled>
              {{ user.user?.email }}
            </ElDropdownItem>
            <ElDropdownItem divided command="logout">登出（服务端撤销）</ElDropdownItem>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
    </div>
  </div>
</template>

<style scoped lang="scss">
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: $navbar-height;
  padding: 0 $space-md 0 $space-sm;
  background: var(--hub-bg-card);

  &__left,
  &__right {
    display: flex;
    align-items: center;
    gap: $space-sm;
  }

  &__icon-btn {
    width: 34px;
    height: 34px;
    padding: 0;
    color: var(--hub-text-regular);

    &:hover {
      color: var(--el-color-primary);
      background: var(--el-color-primary-light-9);
    }
  }

  &__user {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 10px;
    border-radius: $radius-sm;
    cursor: pointer;
    color: var(--hub-text-regular);
    transition: background $duration-fast;

    &:hover {
      background: var(--el-fill-color-light);
    }
  }

  &__user-name {
    max-width: 120px;
    font-size: 13px;
    @include ellipsis;
  }

  &__user-role {
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 11px;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
  }

  // 窄屏：隐藏时间范围（会挤）
  @media (max-width: 1100px) {
    :deep(.el-radio-group) {
      display: none;
    }
  }
}
</style>
