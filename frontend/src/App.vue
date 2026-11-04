<script setup lang="ts">
// 根组件只做一件事：**给路由出口套一层淡入过渡** ✅
// ⚠️ 布局（侧边栏/导航/标签页）在 `layouts/default/index.vue` 里，
//    不放在这里 —— 因为登录页**不要布局** ✅

import { onMounted, watch } from 'vue'
import { ElConfigProvider, ElNotification } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import { useAnalysisStore } from '@/stores/analysis'

// ══════════════════════════════════════════════════════════════
//  分析任务：**全局在后台跑**（2026-11-03 改）
// ══════════════════════════════════════════════════════════════
// 用户反馈："最上面进度显示的按钮布局不好，不行就删掉" ✅
//   → **导航栏胶囊已删** ✅（那个布局总在挤）
//   → 但"**后台运行、不挡人**"这个需求还在 ✅
//   → 现在改成：
//       轮询放在**根组件**（切到任何页面都在跑 ✅）
//       进度显示**只在「数据接入」页**（9 阶段步骤条，空间大、看得清 ✅）
//       **跑完弹一条 ElNotification** —— 不管你在哪个页面都能收到 ✅
const analysis = useAnalysisStore()

onMounted(() => {
  // 刷新 / 重开浏览器 → 自动接上还在跑的那条 ✅
  // ⚠️ 如果那条已经不在了（库被清过）→ store 里会**静默清掉**，不报错 ✅
  analysis.restore()
})

watch(() => [analysis.running, analysis.finished, analysis.failed] as const,
  ([running, finished, failed]) => {
    if (running || (!finished && !failed)) return
    if (analysis.notified) return
    analysis.notified = true
    if (finished) {
      ElNotification({
        title: '分析完成',
        message: '9 个阶段已跑完 —— 去「工作台」看今天该做什么',
        type: 'success',
        duration: 8000,
        onClick: () => { window.location.href = '/dashboard' },
      })
    } else {
      ElNotification({
        title: '分析失败',
        message: analysis.errorMsg || '去「系统日志」看这次跑在哪一步',
        type: 'error',
        duration: 0,
      })
    }
  },
)
</script>

<template>
  <!-- 中文语言包：分页/表格空文案/日期都需要它 -->
  <ElConfigProvider :locale="zhCn">
    <RouterView v-slot="{ Component, route }">
      <!-- 过渡用 `route.path` 做 key：同一组件换参数时也能重渲染 -->
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
