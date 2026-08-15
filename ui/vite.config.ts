import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base=/ui/：生产构建由 dashboard.py 挂在 /ui/ 下服务，资源路径必须带前缀
export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8088', changeOrigin: true },
    },
  },
})
