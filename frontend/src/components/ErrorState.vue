<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  错误态 —— **必须显示 trace_id**
//  为什么：后端每个错误都带 `trace_id`（可在日志里 grep 到那一次请求 ✅）
//         只显示"出错了"等于让用户报障时说不清 ✅ ✗
// ══════════════════════════════════════════════════════════════
import { computed } from 'vue'
import { ElButton } from 'element-plus'

import { ApiError } from '@/api/client'

const props = withDefaults(
  defineProps<{
    error: unknown
    /** 重试回调（不传就不显示按钮）*/
    onRetry?: () => void
  }>(),
  {},
)
const emit = defineEmits<{ retry: [] }>()

const info = computed<{ title: string; message: string; traceId: string; code: string }>(() => {
  const e = props.error
  if (e instanceof ApiError) {
    // 401/403 有专门的跳转/提示，这里一般不出现；出现了也给清楚
    return {
      title: e.status === 0 ? '连不上后端' : `请求失败（HTTP ${e.status}）`,
      message: e.message,
      traceId: e.traceId,
      code: e.code,
    }
  }
  if (e instanceof Error) {
    return { title: '前端出了点问题', message: e.message, traceId: '', code: '' }
  }
  return { title: '未知错误', message: String(e ?? ''), traceId: '', code: '' }
})

function retry(): void {
  emit('retry')
  props.onRetry?.()
}
</script>

<template>
  <div class="error-state">
    <div class="error-state__icon">!</div>
    <div class="error-state__title">{{ info.title }}</div>
    <div class="error-state__message">{{ info.message }}</div>

    <div class="error-state__meta">
      <span v-if="info.code">code: <code>{{ info.code }}</code></span>
      <span v-if="info.traceId">
        trace: <code>{{ info.traceId }}</code>
        <span class="error-state__tip">（后端日志里能 grep 到这一次请求）</span>
      </span>
    </div>

    <ElButton v-if="onRetry || $attrs.onRetry" type="primary" plain size="small" @click="retry">
      重试
    </ElButton>
  </div>
</template>

<style scoped lang="scss">
.error-state {
  @include card;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $space-sm;
  padding: 40px $space-lg;

  &__icon {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    font-weight: 700;
    color: var(--el-color-danger);
    background: var(--el-color-danger-light-9);
  }

  &__title {
    font-size: 15px;
    font-weight: 600;
    color: var(--hub-text-primary);
  }

  &__message {
    max-width: 560px;
    text-align: center;
    font-size: 13px;
    line-height: 1.7;
    color: var(--hub-text-regular);
  }

  &__meta {
    display: flex;
    flex-wrap: wrap;
    gap: $space-md;
    font-size: 11px;
    color: var(--hub-text-secondary);

    code {
      padding: 1px 4px;
      border-radius: 3px;
      background: var(--el-fill-color-light);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
  }

  &__tip {
    margin-left: 4px;
    opacity: 0.8;
  }
}
</style>
