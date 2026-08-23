import { defineConfig, devices } from '@playwright/test'

// E2E 独立验收端口（不与 dev 默认 3001 冲突；3001 常被主仓库/开发实例占用）
const E2E_PORT = Number(process.env.E2E_PORT || 4173)
const E2E_BASE = `http://127.0.0.1:${E2E_PORT}`

export default defineConfig({
  testDir: './tests',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: E2E_BASE,
    trace: 'on-first-retry',
  },
  // E2E 必须自启动当前分支的 dev server（reuseExistingServer=false 防跨分支误测）。
  // strictPort：端口被占即失败，绝不静默漂移到其他端口。
  webServer: {
    command: `npm run dev -- --port ${E2E_PORT} --strictPort`,
    url: E2E_BASE,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
