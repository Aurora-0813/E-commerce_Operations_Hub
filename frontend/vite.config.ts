import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// ⚠️ 后端固定跑在 **8000**（2026-11-01 定："后面我都会启动在 8000" ✅）
const BACKEND = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // 全项目统一用 `@/` 指 src（不用 ../../../ 相对路径）
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 让每个 .vue / .scss 都能直接用变量和 mixin，不用重复 @use
        // ⚠️ 只能放「变量和 mixin」，**不能放会产出 CSS 的规则**（否则每个文件都复制一份）
        additionalData: '@use "@/styles/variables.scss" as *;\n',
      },
    },
  },
  server: {
    port: 5173,
    // 代理：前端仍走 `/api/*`（同源，不用处理 CORS ✅）
    //   为什么用代理而不直连 8000：
    //   ① 生产部署时前后端通常同域 → 代理口径和线上一致
    //   ② 换后端地址只改这一处
    //   ③ 前端代码里所有请求都是 `/api/xxx`，**不出现 host** → 迁移无痛
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
})
