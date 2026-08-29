import { test, expect, type Page } from '@playwright/test'

/** 精简产品 E2E：每日选股 + 专业回测，旧入口回首页。 */

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
      else if (path === '/api/backtest/catalog') body = {
        version: 'test-v1', max_combinations: 512, parameters: [], conditions: [],
        research_boundary: 'EXPLORATORY_ONLY', paper_trading_enabled: false, live_trading_enabled: false,
      }
      else if (path === '/api/backtest/universe') body = {
        classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '测试股票池',
        industries: [{ name: '半导体', count: 30 }], stocks: [], stock_count: 30,
      }
      else if (path === '/api/backtest/latest') body = { task: null }
      else if (path === '/api/backtest/preview') body = {
        can_run: true,
        estimated_work: { combinations: 144, stocks: 30, sample_step: 10, note: '后台运行并持久化' },
        prepared: {
          contract_version: 'test-v1', strategy: 'A', sample_step: 10, max_codes: 600,
          parameters: {}, conditions: [], input_hash: 'test-input-hash',
          parameter_space: { count: 144, sha256: 'grid', horizon: 265, signal_group_count: 24, exit_group_count: 6, invalid_signal_combinations: 0 },
          universe: {
            industries: [], codes: Array.from({ length: 30 }, (_, index) => `${String(index).padStart(6, '0')}.SZ`),
            source: 'CURRENT_ALL', count: 30, sha256: 'universe',
            classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
          },
          windows: { mode: 'auto', is: ['20230101', '20241231'], oos: ['20250101', '20260828'], wf: [], n_dates: 800 },
        },
      }
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

test('旧实验、纸面与机构入口全部回到每日选股', async ({ page }) => {
  await mockBackendApi(page)
  for (const path of ['/lab', '/paper', '/v2/monitor']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: '每日选股' })).toBeVisible()
  }
})

test('侧栏只保留两个日用入口', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: /每日选股/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /专业回测/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /纸面仿真/ })).toHaveCount(0)
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

test('@a11y 键盘可达专业回测入口', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  const backtest = page.getByRole('button', { name: /专业回测/ })
  await backtest.focus()
  await expect(backtest).toBeFocused()
})

test('专业回测必须先预览参数空间', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/backtest')
  await expect(page.getByRole('heading', { name: '多参数专业回测' })).toBeVisible()
  const run = page.getByRole('button', { name: '启动专业回测' })
  await expect(run).toBeDisabled()
  await page.getByRole('button', { name: '检查参数空间' }).click()
  await expect(page.getByText('有效组合').locator('..').getByText('144')).toBeVisible()
  await expect(run).toBeEnabled()
})
