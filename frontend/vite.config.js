import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时同源代理 API 与管理后台，保留 Django 会话和 CSRF 校验。
const backend = { target: 'http://127.0.0.1:8010', changeOrigin: false }
const proxy = Object.fromEntries(['/api', '/admin', '/static'].map(path => [path, backend]))
export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 5174, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4174, proxy }
})
