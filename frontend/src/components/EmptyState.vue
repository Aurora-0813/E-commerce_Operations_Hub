<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  空态 —— **必须带"怎么让它有数据"**
//  为什么：我们的空态是真的（/notify、/knowledge 可能 0 条；/brief 可能 available:false）
//         只显示"暂无数据"等于让用户卡死 ✅ ✗
// ══════════════════════════════════════════════════════════════
import { ElEmpty } from 'element-plus'

withDefaults(
  defineProps<{
    /** 一句话说明"为什么是空的" */
    description?: string
    /** **怎么办**（这是和 ElEmpty 的区别 ✅）*/
    hint?: string
    /** 要展示的命令（会渲染成代码块）*/
    command?: string
    /** 缩到更小（放在卡片里用）*/
    compact?: boolean
  }>(),
  { description: '暂无数据', hint: '', command: '', compact: false },
)
</script>

<template>
  <div :class="['empty-state', { 'empty-state--compact': compact }]">
    <ElEmpty :description="description" :image-size="compact ? 60 : 90">
      <template #description>
        <div class="empty-state__desc">{{ description }}</div>
      </template>
    </ElEmpty>
    <div v-if="hint" class="empty-state__hint">{{ hint }}</div>
    <code v-if="command" class="empty-state__cmd">{{ command }}</code>
  </div>
</template>

<style scoped lang="scss">
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: $space-lg 0;

  &--compact {
    padding: $space-sm 0;
  }

  &__desc {
    font-size: 13px;
    color: var(--hub-text-secondary);
  }

  &__hint {
    margin-top: $space-sm;
    max-width: 420px;
    text-align: center;
    font-size: 12px;
    line-height: 1.7;
    color: var(--hub-text-secondary);
  }

  &__cmd {
    margin-top: $space-sm;
    padding: 4px 10px;
    border-radius: $radius-sm;
    font-size: 12px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
  }
}
</style>
