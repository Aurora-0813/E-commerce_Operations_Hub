<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  7 号页：通知中心（㉑）
//  数据源：GET /api/notify（含 `unread`）、POST /api/notify/{id}/read
//  ⚠️ **现在可能是 0 条** —— 通知靠 `hub notify --scan` 产生（部署时接 cron ✅）
//     → 空态必须告诉用户"怎么让它有数据" ✅
// ══════════════════════════════════════════════════════════════
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElButton, ElCard, ElMessage, ElOption, ElPagination, ElSelect,
} from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import { getNotify, type NotifyItem } from '@/api/read'
import { readNotify } from '@/api/write'
import EmptyState from '@/components/EmptyState.vue'
import LabelTag from '@/components/LabelTag.vue'
import { EVENT } from '@/config/labels'
import ErrorState from '@/components/ErrorState.vue'

const router = useRouter()

const loading = ref(true)
const error = ref<unknown>(null)
const items = ref<NotifyItem[]>([])
const total = ref(0)
const unread = ref(0)
const page = ref(1)
const size = ref(20)
const eventType = ref<string>('')

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const d = await getNotify(size.value)
    // ⚠️ `getNotify` 只传了 limit；这里要筛选 + 分页 → 再请求一次带参
    const { data } = await (await import('@/api/client')).default.get<{
      total: number; unread: number; items: NotifyItem[]
    }>('/api/notify', {
      params: {
        limit: size.value,
        offset: (page.value - 1) * size.value,
        event_type: eventType.value || undefined,
      },
    })
    items.value = data.items
    total.value = data.total
    unread.value = data.unread > 0 ? data.unread : d.unread
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
watch([page, eventType], () => void load())
watch(size, () => { page.value = 1; void load() })

// ---------- 事件类型 ----------
const EVENT_LABEL: Record<string, { text: string; type: 'danger' | 'warning' | 'primary' | 'success' | 'info' }> = {
  'proposal.pending': { text: '提案待批', type: 'warning' },
  'retro.due': { text: '复盘到期', type: 'primary' },
  dead_letter: { text: '死信', type: 'danger' },
  'agent.done': { text: 'Agent 需人工', type: 'info' },
  test: { text: '测试', type: 'info' },
}
const STATUS_LABEL: Record<string, string> = {
  PENDING: '待发', SENT: '已发', SKIPPED: '未发（未配渠道）',
  FAILED: '失败', READ: '已读',
}

function evMeta(t: string): { text: string; type: 'danger' | 'warning' | 'primary' | 'success' | 'info' } {
  return EVENT_LABEL[t] ?? { text: t, type: 'info' }
}

/** 合并条数（`payload.count` —— 后端 10 分钟合并 ✅）*/
function mergedCount(it: NotifyItem): number {
  const c = (it.payload as Record<string, unknown> | undefined)?.count
  return typeof c === 'number' ? c : 1
}

// ---------- 标记已读 ----------
const actingId = ref<number | null>(null)
async function markRead(it: NotifyItem): Promise<void> {
  if (it.status === 'READ') return
  actingId.value = it.id
  try {
    await readNotify(it.id)
    it.status = 'READ'
    unread.value = Math.max(0, unread.value - 1)
    ElMessage.success('已标记已读')
  } finally {
    actingId.value = null
  }
}

/** 全部标已读（只处理当前页 ✅ —— 全量要后端批量接口，暂无）*/
async function markAllRead(): Promise<void> {
  const targets = items.value.filter((i) => i.status !== 'READ')
  for (const t of targets) {
    try {
      await readNotify(t.id)
      t.status = 'READ'
    } catch {
      // 单条失败不影响其他
    }
  }
  unread.value = Math.max(0, unread.value - targets.length)
  ElMessage.success(`已把本页 ${targets.length} 条标为已读`)
}

/** 点一条 → 跳到对应资源 ✅ */
function open(it: NotifyItem): void {
  if (it.event_type === 'proposal.pending' && it.resource_id) {
    void router.push(`/proposals/${it.resource_id}`)
  } else if (it.event_type === 'retro.due') {
    void router.push('/retro')
  } else if (it.event_type === 'dead_letter') {
    void router.push('/ops')
  } else if (it.event_type === 'agent.done') {
    void router.push('/agent')
  }
  void markRead(it)
}

const unreadText = computed(() => (unread.value > 0 ? `${unread.value} 条未读` : '全部已读 ✅'))
</script>

<template>
  <div class="notify">
    <div class="notify__head">
      <div>
        <h2 class="notify__title">通知中心</h2>
        <div class="notify__sub">
          {{ unreadText }} · 共 {{ total }} 条
          <span class="notify__note">（同一事件 10 分钟内合并成一条）</span>
        </div>
      </div>
      <div class="notify__actions">
        <ElSelect v-model="eventType" size="default" placeholder="全部类型" clearable
                  class="notify__filter">
          <ElOption value="proposal.pending" label="提案待批" />
          <ElOption value="retro.due" label="复盘到期" />
          <ElOption value="dead_letter" label="死信" />
          <ElOption value="agent.done" label="Agent 需人工" />
        </ElSelect>
        <ElButton :disabled="!unread" @click="markAllRead">本页全标已读</ElButton>
        <ElButton :icon="Refresh" :loading="loading" @click="load">刷新</ElButton>
      </div>
    </div>

    <ErrorState v-if="error" :error="error" @retry="load" />

    <ElCard v-else shadow="never">
      <!-- ⚠️ 0 条是正常的：通知靠扫描产生 -->
      <EmptyState
        v-if="!loading && !items.length"
        description="没有通知"
        hint="通知不会自己产生 —— 要扫一次触发源（提案待批 / 复盘到期 / 死信 / Agent 转人工）"
        command="hub notify --scan"
      />

      <div v-else v-loading="loading" class="notify__list">
        <div v-for="it in items" :key="it.id" class="notify__item"
             :class="{ 'is-unread': it.status !== 'READ' }" @click="open(it)">
          <div class="notify__item-left">
            <LabelTag :dict="EVENT" :value="it.event_type" effect="dark" />
            <span class="notify__item-res">{{ it.resource_id.slice(0, 12) }}</span>
          </div>

          <div class="notify__item-mid">
            <div class="notify__item-title">
              {{ evMeta(it.event_type).text }}
              <span v-if="mergedCount(it) > 1" class="notify__merged">
                合并 {{ mergedCount(it) }} 条
              </span>
            </div>
            <div class="notify__item-meta">
              {{ it.channel }} · {{ STATUS_LABEL[it.status] ?? it.status }}
              <span v-if="it.attempts"> · 尝试 {{ it.attempts }} 次</span>
              · {{ (it.created_at ?? '').slice(0, 16).replace('T', ' ') }}
            </div>
          </div>

          <div class="notify__item-right">
            <span v-if="it.status === 'READ'" class="notify__read">已读</span>
            <ElButton v-else size="small" text type="primary"
                      :loading="actingId === it.id" @click.stop="markRead(it)">
              标已读
            </ElButton>
          </div>
        </div>
      </div>

      <div v-if="total > size" class="notify__pager">
        <ElPagination v-model:current-page="page" v-model:page-size="size"
                      :total="total" :page-sizes="[20, 50, 100]"
                      layout="total, sizes, prev, pager, next" background small />
      </div>
    </ElCard>
  </div>
</template>

<style scoped lang="scss">
.notify {
  display: flex;
  flex-direction: column;
  gap: $space-md;

  &__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-md;
  }

  &__title {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: var(--hub-text-secondary);
  }

  &__note {
    opacity: 0.75;
  }

  &__actions {
    display: flex;
    gap: $space-sm;
  }

  &__filter {
    width: 150px;
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__item {
    display: flex;
    align-items: center;
    gap: $space-sm;
    padding: 10px 12px;
    border: 1px solid var(--hub-border-light);
    border-radius: $radius-sm;
    cursor: pointer;
    transition: all $duration-fast;

    &:hover {
      border-color: var(--el-color-primary-light-5);
      background: var(--el-color-primary-light-9);
    }

    // 未读：左侧一条蓝线 + 略深背景
    &.is-unread {
      border-left: 3px solid var(--el-color-primary);
      background: var(--el-fill-color-lighter);
    }
  }

  &__item-left {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 0 0 auto;
  }

  &__item-res {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10px;
    color: var(--hub-text-secondary);
  }

  &__item-mid {
    flex: 1 1 auto;
    min-width: 0;
  }

  &__item-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    @include ellipsis;
  }

  &__merged {
    padding: 0 5px;
    border-radius: 999px;
    font-size: 10px;
    color: var(--el-color-warning-dark-2);
    background: var(--el-color-warning-light-9);
  }

  &__item-meta {
    margin-top: 2px;
    font-size: 11px;
    color: var(--hub-text-secondary);
  }

  &__item-right {
    flex: 0 0 auto;
  }

  &__read {
    font-size: 11px;
    color: var(--hub-text-placeholder);
  }

  &__pager {
    display: flex;
    justify-content: flex-end;
    margin-top: $space-md;
  }
}
</style>
