<script setup lang="ts">
// 占位页：批 1 只做「登录 + 工作台」，其余 6 个页面留占位
// ⚠️ **不要塞假数据**冒充做完了 —— 这里明确写"还没做" ✅

import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElEmpty } from 'element-plus'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? '这个页面'))

/** 每页在路线图里的阶段（写清楚，避免"看起来都有了" ✅）*/
const PLAN: Record<string, string> = {
  '/problems': '批 2 · 问题全景（⑥⑦⑧⑨⑩ 五张图）',
  '/proposals': '批 2 · 待批提案列表',
  '/agent': '批 3 · Agent 任务（⑮⑯ 轨迹 + 每步 token）',
  '/retro': '批 3 · 复盘报告（⑪⑫⑬⑭ DID 区间图）',
  '/notify': '批 4 · 通知中心（㉑ 未读数 + 标记已读）',
  '/knowledge': '批 4 · 知识库（㉒ DRAFT/PUBLISHED + effective）',
  '/ops': '批 4 · 运维台（⑰⑱ 日志 + 上传 + 死信重放）',
}
const plan = computed(() => PLAN[route.path] ?? '待排期')
</script>

<template>
  <div class="placeholder">
    <ElEmpty :description="`${title} —— 还没做`">
      <div class="placeholder__plan">
        <div class="placeholder__tag">{{ plan }}</div>
        <div class="placeholder__hint">
          这一页的接口<b>已经就绪</b>（见
          <code>docs/API.md</code>），只是前端还没写。
          <br />
          先做「登录 + 今日工作台」，是为了先跑通<b>一条真实链路</b>，而不是铺 10 个空页面。
        </div>
      </div>
    </ElEmpty>
  </div>
</template>

<style scoped lang="scss">
.placeholder {
  @include card;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 420px;

  &__plan {
    margin-top: $space-md;
    text-align: center;
  }

  &__tag {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
  }

  &__hint {
    margin-top: $space-sm;
    max-width: 460px;
    font-size: 12px;
    line-height: 1.7;
    color: var(--hub-text-secondary);

    code {
      padding: 1px 4px;
      border-radius: $radius-sm;
      background: var(--el-fill-color-light);
    }
  }
}
</style>
