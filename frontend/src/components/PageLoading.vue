<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  骨架屏（**不用转圈**）
//  为什么用骨架而不是 Loading 转圈：
//    看板一屏有 5~8 个区块，转圈只能表示"在转"，骨架能表示"**结构长什么样**" ✅
//    而且不会在加载完后"跳一下"（骨架和内容的尺寸一致 ✅）
// ══════════════════════════════════════════════════════════════
withDefaults(
  defineProps<{
    /** 骨架块数（按页面复杂度传）*/
    rows?: number
    /** 是否有图表区（图表用方块骨架，不用文字条 ✅）*/
    chart?: boolean
  }>(),
  { rows: 3, chart: false },
)
</script>

<template>
  <div class="skeleton">
    <!-- 顶部标题条 -->
    <div class="skeleton__bar skeleton__bar--title" />

    <!-- 图表区 -->
    <div v-if="chart" class="skeleton__chart" />

    <!-- 数据行 -->
    <div v-for="i in rows" :key="i" class="skeleton__row">
      <div class="skeleton__bar" :style="{ width: `${88 - i * 7}%` }" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.skeleton {
  @include card;
  padding: $space-lg;

  &__bar {
    height: 14px;
    margin-bottom: $space-md;
    border-radius: $radius-sm;
    background: linear-gradient(
      90deg,
      var(--el-fill-color-light) 25%,
      var(--el-fill-color) 37%,
      var(--el-fill-color-light) 63%
    );
    background-size: 400% 100%;
    // ⚠️ 尊重"减少动效"：系统开了 reduce 就不闪
    animation: skeleton-shine 1.4s ease infinite;

    &--title {
      width: 180px;
      height: 18px;
      margin-bottom: $space-lg;
    }
  }

  &__chart {
    height: 200px;
    margin-bottom: $space-lg;
    border-radius: $radius-base;
    background: var(--el-fill-color-lighter);
    animation: skeleton-shine 1.4s ease infinite;
  }

  &__row {
    margin-bottom: $space-sm;
  }
}

@keyframes skeleton-shine {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton__bar,
  .skeleton__chart {
    animation: none;
  }
}
</style>
