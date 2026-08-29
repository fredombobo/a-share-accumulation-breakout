import { test, expect, type Page } from '@playwright/test'

/** 精简产品 E2E：旧入口回首页、仅两项导航、窄屏与键盘可达。 */

async function mockBackendApi(page: Page) {
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const path = new URL(route.request().url()).pathname
      let body: unknown = {}
      if (path === '/api/overview') body = { as_of: '20260828', pool: 'A', count: 0, items: [] }
      else if (path === '/api/health') body = { status: 'ok', build_version: 'test', live_trading_enabled: false }
      else if (path === '/api/setup-status') body = { ready: true }
      else if (path === '/api/sector-flow') body = { items: [] }
      else if (path === '/api/money-heatmap') body = { trade_date: '20260828', total_wan: 0, items: [] }
      else if (path === '/api/today') body = { next_action: 'NONE', title: '今日无需操作', reason: '测试', primary_label: '完成' }
      else if (path === '/api/scan/status') body = { status: 'idle' }
      else if (path === '/api/sync/status') body = { status: 'idle', latest_daily: '20260828', failed_dates: [] }
      else if (path === '/api/v2/platform/status') {
        body = { product: 'accumulation_breakout', default_port: 8001, build_version: 'test', readiness: 'BLOCKED', flags: {} }
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
    },
  )
}

test('旧实验与机构入口全部回到每日选股', async ({ page }) => {
  await mockBackendApi(page)
  for (const path of ['/lab', '/backtest', '/v2/monitor']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: '每日选股' })).toBeVisible()
  }
})

test('侧栏只保留两个日用入口', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: /每日选股/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /纸面仿真/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /策略实验室/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /六形态/ })).toHaveCount(0)
})

test('@a11y 390px 首页不横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockBackendApi(page)
  await page.goto('/')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(overflow).toBe(false)
})

test('@a11y 键盘可达纸面仿真入口', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  const paper = page.getByRole('button', { name: /纸面仿真/ })
  await paper.focus()
  await expect(paper).toBeFocused()
})
