import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backendUrl = env.AB_BACKEND_URL || 'http://127.0.0.1:8001'

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 3001,
      proxy: {
        '/api': { target: backendUrl, changeOrigin: true },
      },
    },
  }
})
