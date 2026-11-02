import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

// ⚠️ 样式顺序很重要：
//   ① Element 基础样式 → ② 暗色变量 → ③ 我们的主题覆盖 → ④ 全局
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/styles/theme.scss'
import '@/styles/index.scss'

import App from './App.vue'
import router from './router'
import { setNotifier, setUnauthorizedHandler } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'

const app = createApp(App)

// ---------- Pinia ----------
const pinia = createPinia()
app.use(pinia)

// ---------- 主题初始化（**挂载前**，否则会闪一下白 ✅）----------
const appStore = useAppStore(pinia)
appStore.init()

// ---------- 把两个 UI 回调注入 axios 封装 ----------
//   为什么不在 client.ts 里直接 import el-message / router：
//     会形成 client → element-plus/router → stores → client 的**循环依赖** ✅
setNotifier((msg: string) => {
  ElMessage({ message: msg, type: 'error', duration: 4000, showClose: true })
})
setUnauthorizedHandler(() => {
  const user = useUserStore(pinia)
  user.forceClear()
  const current = router.currentRoute.value
  if (current.path !== '/login') {
    void router.replace({ path: '/login', query: { redirect: current.fullPath } })
  }
})

// ---------- Element Plus ----------
app.use(ElementPlus, { locale: zhCn, size: 'default' })

// ---------- 路由 ----------
app.use(router)

app.mount('#app')
