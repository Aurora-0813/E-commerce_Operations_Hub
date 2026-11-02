<script setup lang="ts">
// 根组件只做一件事：**给路由出口套一层淡入过渡** ✅
// ⚠️ 布局（侧边栏/导航/标签页）在 `layouts/default/index.vue` 里，
//    不放在这里 —— 因为登录页**不要布局** ✅

import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<template>
  <!-- 中文语言包：分页/表格空文案/日期都需要它 ✅ -->
  <ElConfigProvider :locale="zhCn">
    <RouterView v-slot="{ Component, route }">
      <!-- 过渡用 `route.path` 做 key：同一组件换参数时也能重渲染 ✅ -->
      <Transition name="fade" mode="out-in">
        <component :is="Component" :key="route.path" />
      </Transition>
    </RouterView>
  </ElConfigProvider>
</template>

<style>
/* 全局只留最必要的：让 #app 撑满 */
#app {
  height: 100%;
}
</style>
