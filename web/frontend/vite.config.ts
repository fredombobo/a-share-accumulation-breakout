/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backendUrl = env.AB_BACKEND_URL || 'http://127.0.0.1:8001'
  // cacheDir 可经 VITE_CACHE_DIR 外置（默认 node_modules/.vite），
  // 供受限文件系统环境（如沙箱）把依赖预打包缓存写到可写目录。
  const cacheDir = env.VITE_CACHE_DIR || 'node_modules/.vite'

  return {
    plugins: [react(), tailwindcss()],
    cacheDir,
    server: {
      port: 3001,
      proxy: {
        '/api': { target: backendUrl, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./tests/setup.ts'],
      include: ['./tests/**/*.test.{ts,tsx}'],
    },
  }
})
