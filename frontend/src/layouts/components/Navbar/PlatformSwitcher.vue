<script setup lang="ts">
// ══════════════════════════════════════════════════════════════
//  平台 + 店铺 联动切换器
//  ⚠️ 店铺维度**后端还没有**（38 张表里没有 shop）→ 显示"全部店铺"并禁用 ✅
//     不假装支持：点了没反应比"假装切了"好 ✅
// ══════════════════════════════════════════════════════════════
import { computed } from 'vue'
import { ElOption, ElSelect, ElTag, ElTooltip } from 'element-plus'

import { PLATFORM_LIST } from '@/config/platform'
import { usePlatformStore } from '@/stores/platform'

const store = usePlatformStore()

const platform = computed({
  get: () => store.platform,
  set: (v: 'taobao' | 'jd') => store.setPlatform(v),
})

const shopOptions = [{ id: '__all__', name: '全部店铺' }]
</script>

<template>
  <div class="platform-switcher">
    <!-- 平台：色块 + 名字 -->
    <ElSelect v-model="platform" class="platform-switcher__platform" size="default">
      <ElOption v-for="p in PLATFORM_LIST" :key="p.key" :value="p.key" :label="p.label">
        <span class="platform-switcher__dot" :style="{ background: p.color }" />
        <span>{{ p.label }}</span>
      </ElOption>
    </ElSelect>

    <!-- 店铺：<b>后端暂无此维度</b> → 禁用 + tooltip 说明原因 -->
    <ElTooltip content="后端数据里还没有「店铺」维度（38 张表里没有 shop）→ 先按全部店铺看"
               placement="bottom" :show-after="200">
      <ElSelect v-model="shopOptions[0].id" class="platform-switcher__shop"
                size="default" disabled>
        <ElOption v-for="s in shopOptions" :key="s.id" :value="s.id" :label="s.name" />
      </ElSelect>
    </ElTooltip>

    <ElTag v-if="!store.shop" size="small" type="info" effect="plain">全部店铺</ElTag>
  </div>
</template>

<style scoped lang="scss">
.platform-switcher {
  display: flex;
  align-items: center;
  gap: $space-sm;

  &__platform {
    width: 96px;
  }

  &__shop {
    width: 120px;
  }

  &__dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-right: 6px;
    border-radius: 50%;
    vertical-align: middle;
  }
}
</style>
