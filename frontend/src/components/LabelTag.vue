<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  通用中文标签（LabelTag.vue）—— **页面上不许再出现英文枚举**
// ══════════════════════════════════════════════════════════════
// 用法：
//   <LabelTag :dict="STATUS" :value="row.status" />
//   <LabelTag :dict="ACTION" :value="log.action" show-hint />
//
// **为什么要这个组件**（2026-11-02，用户实测反馈）：
//   · 复盘页显示 `WAITING` / `INCONCLUSIVE` / `SIGNIFICANT` ✅ ✗
//   · 操作日志显示 `proposal.approve` / `data.ingest` ✅ ✗
//   · 通知里显示"死信"三个字（用户说看不懂 ✅）
//   → 统一走这里：**中文 + 颜色 + tooltip 解释** ✅
//
// ⚠️ `ElTooltip` 包 `ElTag` 是**官方用法** ✅
//    （之前踩的 vnode 报错是 `ElTooltip` 包 `ElIcon` 放进 `ElMenuItem` ✅ 那是另一个坑）
import { computed } from 'vue'
import { ElTag, ElTooltip } from 'element-plus'

import { labelOf, type Label, type Tone } from '@/config/labels'

const props = withDefaults(
  defineProps<{
    /** 字典（STATUS / ACTION / EVENT / TOOL / TOPIC / RISK / CONCLUSION …）*/
    dict: Record<string, Label>
    /** 后端返回的原始值 */
    value: string | null | undefined
    /** 值缺失时显示什么 */
    fallback?: string
    /** 是否显示 tooltip 解释（默认**显示**，因为用户说看不懂 ✅）*/
    showHint?: boolean
    /** 尺寸 */
    size?: 'small' | 'default' | 'large'
    /** 是否用深色底（默认 plain 更克制 ✅）*/
    effect?: 'dark' | 'light' | 'plain'
    /** 纯文本（不要标签样式，用在表格描述性文字里 ✅）*/
    textOnly?: boolean
  }>(),
  { fallback: '—', showHint: true, size: 'small', effect: 'plain', textOnly: false },
)

const label = computed<Label>(() => labelOf(props.dict, props.value, props.fallback))
const tone = computed<Tone>(() => label.value.tone ?? 'info')
</script>

<template>
  <!-- 纯文本模式 -->
  <span v-if="textOnly" :title="label.hint">{{ label.text }}</span>

  <!-- 有解释 → 包 tooltip -->
  <ElTooltip v-else-if="showHint && label.hint" :content="label.hint"
             placement="top" :show-after="150">
    <ElTag :type="tone" :size="size" :effect="effect" class="label-tag">
      {{ label.text }}
    </ElTag>
  </ElTooltip>

  <!-- 没解释 → 直接标签 -->
  <ElTag v-else :type="tone" :size="size" :effect="effect" class="label-tag">
    {{ label.text }}
  </ElTag>
</template>

<style scoped lang="scss">
.label-tag {
  // 中文标签不要固定宽度，让它自适应
  font-weight: 500;
}
</style>
