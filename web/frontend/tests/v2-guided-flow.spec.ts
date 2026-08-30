import { test, expect, type Page } from '@playwright/test'

/** 精简产品 E2E：每日选股 + 研究回测，旧入口回首页。 */

async function mockBackendApi(page: Page, options: { activeScan?: boolean; onPreview?: (body: any) => void; onManualProfile?: (body: any) => void } = {}) {
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const url = new URL(route.request().url())
      const path = url.pathname
      const classification = url.searchParams.get('classification') || 'industry'
      const classificationMeta = classification === 'market'
        ? { classification: 'market', classification_title: '上市板块', group_label: '板块' }
        : classification === 'area'
          ? { classification: 'area', classification_title: '地域', group_label: '地区' }
          : { classification: 'industry', classification_title: '细分行业', group_label: '行业' }
      let body: unknown = {}
      if (path === '/api/overview') body = { as_of: '20260828', pool: 'A', count: 0, items: [] }
      else if (path === '/api/health') body = { status: 'ok', build_version: 'test', live_trading_enabled: false }
      else if (path === '/api/setup-status') body = { ready: true }
      else if (path === '/api/classifications') body = {
        default: 'industry', limitations: '当前分类快照',
        items: [
          { key: 'industry', title: '细分行业', group_label: '行业', description: '产业细分', pit_status: 'CURRENT_SNAPSHOT_ONLY', available: true, group_count: 2, coverage_pct: 100, examples: ['半导体'] },
          { key: 'market', title: '上市板块', group_label: '板块', description: '上市制度分组', pit_status: 'CURRENT_SNAPSHOT_ONLY', available: true, group_count: 2, coverage_pct: 100, examples: ['创业板'] },
          { key: 'area', title: '地域', group_label: '地区', description: '公司注册地', pit_status: 'CURRENT_SNAPSHOT_ONLY', available: true, group_count: 2, coverage_pct: 99.8, examples: ['广东'] },
        ],
      }
      else if (path === '/api/sector-flow') body = { ...classificationMeta, dates: [], days: 10, groups: {}, industries: {}, top_in: [], top_out: [] }
      else if (path === '/api/money-heatmap') body = { ...classificationMeta, trade_date: '20260828', total_wan: 0, items: [] }
      else if (path === '/api/today') body = { next_action: 'NONE', title: '今日无需操作', reason: '测试', primary_label: '完成' }
      else if (path === '/api/scan/status') body = options.activeScan
        ? {
            id: 'scan-global-progress', status: 'running', stage: '资金流核验', progress: 46,
            cancel_requested: false,
            started_at: new Date(Date.now() - 90_000).toISOString(),
            updated_at: new Date().toISOString(),
          }
        : { status: 'idle' }
      else if (path === '/api/sync/status') body = { status: 'idle', latest_daily: '20260828', failed_dates: [] }
      else if (path === '/api/backtest/catalog') body = {
        version: 'test-v1', max_combinations: 5120, long_running_warning_combinations: 512, parameters: [], conditions: [],
        research_boundary: 'EXPLORATORY_ONLY', paper_trading_enabled: false, live_trading_enabled: false,
      }
      else if (path === '/api/backtest/universe') body = {
        ...classificationMeta,
        classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '测试股票池',
        classifications: [
          { key: 'industry', title: '细分行业', group_label: '行业', description: '产业细分', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 1 },
          { key: 'market', title: '上市板块', group_label: '板块', description: '上市制度分组', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
          { key: 'area', title: '地域', group_label: '地区', description: '公司注册地', pit_status: 'CURRENT_SNAPSHOT_ONLY', group_count: 2 },
        ],
        groups: classification === 'market' ? [{ name: '创业板', count: 30 }, { name: '主板', count: 60 }] : [{ name: '半导体', count: 30 }],
        industries: classification === 'industry' ? [{ name: '半导体', count: 30 }] : [], stocks: [], stock_count: 90,
      }
      else if (path === '/api/backtest/latest') body = { task: null }
      else if (path === '/api/backtest/profile/manual') {
        const requestBody = route.request().postDataJSON()
        options.onManualProfile?.(requestBody)
        body = {
          active: {
            profile_id: 'manual-daily-research-scan', name: '用户手工研究参数', version: 'manual-test', schema_version: 3,
            is_default: false, status: 'active', storage_status: 'active', config_hash: 'manual-hash',
            entry: requestBody.parameters,
            exit_reference: requestBody.parameters,
            required_scan_days: 190,
            source: { kind: 'MANUAL_RESEARCH', task_id: null, evidence: { backtest_validated: false } }, notes: [],
          },
          history: [], idempotent: false,
          boundary: {
            scope: 'DAILY_A_POOL_TECHNICAL_ENTRY', manual_activation_required: true,
            automatic_promotion: false, b_pool_uses_profile: false,
            daily_extra_gates: ['资金流', '基本面'],
            notice: '手工参数只用于个人研究学习，不构成荐股。',
          },
          live_trading_enabled: false,
        }
      }
      else if (path === '/api/backtest/profile') body = {
        active: {
          profile_id: 'default', name: '系统默认', version: '1.0.0', schema_version: 1,
          is_default: true, status: 'active', storage_status: 'built_in', config_hash: 'default-hash',
          entry: {
            box_min_days: 20, box_max_days: 125, box_max_amp: 0.26, breakout_vol_ratio: 1.6,
            breakout_chg_min: 0.02, breakout_chg_max: 0.095, breakout_vs_recent_vol_ratio: 1.3,
            breakout_window_days: 5, require_structure: true,
          },
          exit_reference: { vol_ratio_min: 1.5, stop_pct: 0.07, target_pct: 0.12, exit_window: 10, strong_reset: 3 }, required_scan_days: 160,
          source: { kind: 'BUILT_IN', task_id: null }, notes: [],
        },
        history: [],
        boundary: {
          scope: 'DAILY_A_POOL_TECHNICAL_ENTRY', manual_activation_required: true,
          automatic_promotion: false, b_pool_uses_profile: false,
          daily_extra_gates: ['资金流', '基本面'],
          notice: '只统一 A 池技术入场参数；每日额外门禁继续执行。',
        },
        live_trading_enabled: false,
      }
      else if (path === '/api/backtest/preview') {
        options.onPreview?.(route.request().postDataJSON())
        body = {
        can_run: true,
        estimated_work: { combinations: 432, stocks: 30, sample_step: 10, long_running: false, warning_threshold: 512, note: '后台运行并持久化' },
        prepared: {
          contract_version: 'test-v1', strategy: 'A', sample_step: 10, max_codes: 600,
          parameters: {}, conditions: [], input_hash: 'test-input-hash',
          parameter_space: { count: 432, sha256: 'grid', horizon: 265, signal_group_count: 24, exit_group_count: 18, invalid_signal_combinations: 0, long_running: false, long_running_warning_combinations: 512 },
          universe: {
            classification: 'industry', classification_title: '细分行业', group_label: '行业', groups: [],
            industries: [], codes: Array.from({ length: 30 }, (_, index) => `${String(index).padStart(6, '0')}.SZ`),
            source: 'CURRENT_ALL', count: 30, sha256: 'universe',
            classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
          },
          windows: { mode: 'auto', is: ['20230101', '20241231'], oos: ['20250101', '20260828'], wf: [], n_dates: 800 },
        },
      }
      }
      else if (path === '/api/v2/platform/status') {
        body = { product: 'accumulation_breakout', default_port: 8001, build_version: 'test', readiness: 'BLOCKED', flags: {} }
      }
      await route.fulfill({
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

test('侧栏保留两个业务入口并提供独立使用说明', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  await expect(page.getByText('系统默认')).toBeVisible()
  await expect(page.getByRole('button', { name: /每日选股/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /研究回测/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /使用说明/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /纸面仿真/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /策略实验室/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /六形态/ })).toHaveCount(0)
})

test('@a11y 站内说明书可访问且 390px 不溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockBackendApi(page)
  await page.goto('/guide')
  await expect(page.getByRole('heading', { name: '从更新行情到读懂回测' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '板块分类标准' })).toBeVisible()
  await expect(page.getByText(/申万、中信和概念板块/)).toBeVisible()
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(overflow).toBe(false)
})

test('首页和研究回测可切换上市板块分类并冻结选择', async ({ page }) => {
  let previewBody: any = null
  await mockBackendApi(page, { onPreview: (body) => { previewBody = body } })
  await page.goto('/')
  await page.getByLabel('资金板块分类标准').selectOption('market')
  await expect(page.getByRole('heading', { name: /上市板块资金热力图/ })).toBeVisible()

  await page.getByRole('button', { name: /研究回测/ }).click()
  await expect(page).toHaveURL(/\/backtest$/)
  await expect(page.getByRole('heading', { name: '多参数研究回测' })).toBeVisible()
  await page.getByLabel('分类标准').selectOption('market')
  const growthBoard = page.getByRole('checkbox', { name: /创业板/ })
  await expect(growthBoard).toBeVisible()
  await growthBoard.check()
  await page.getByRole('button', { name: '检查参数空间' }).click()
  await expect(page.getByText('有效组合').locator('..').getByText('432')).toBeVisible()
  expect(previewBody.universe).toEqual({ classification: 'market', groups: ['创业板'], codes: [] })
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

test('@a11y 键盘可达研究回测入口', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/')
  const backtest = page.getByRole('button', { name: /研究回测/ })
  await backtest.focus()
  await expect(backtest).toBeFocused()
})

test('研究回测必须先预览参数空间', async ({ page }) => {
  await mockBackendApi(page)
  await page.goto('/backtest')
  await expect(page.getByRole('heading', { name: '多参数研究回测' })).toBeVisible()
  const run = page.getByRole('button', { name: '启动研究回测' })
  await expect(run).toBeDisabled()
  await page.getByRole('button', { name: '检查参数空间' }).click()
  await expect(page.getByText('有效组合').locator('..').getByText('432')).toBeVisible()
  await expect(run).toBeEnabled()
})

test('@a11y 扫描进度切页后仍显眼可见且窄屏不溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockBackendApi(page, { activeScan: true })
  await page.goto('/')

  const progress = page.getByRole('progressbar', { name: '全市场扫描进度' })
  await expect(progress).toBeVisible()
  await expect(progress).toHaveAttribute('aria-valuenow', '46')
  await expect(page.getByText('资金流核验')).toBeVisible()

  await page.getByRole('button', { name: /研究回测/ }).click()
  await expect(page).toHaveURL(/\/backtest$/)
  await expect(progress).toBeVisible()
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(overflow).toBe(false)
})

test('今日扫描参数可手工输入且明确标记未回测验证', async ({ page }) => {
  let saved: any = null
  await mockBackendApi(page, { onManualProfile: (body) => { saved = body } })
  await page.goto('/')
  await page.getByRole('button', { name: '手动设置研究参数' }).click()
  await expect(page.getByRole('heading', { name: '手工今日研究参数' })).toBeVisible()
  await page.getByLabel('止盈（%）').fill('18')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '确认保存手工参数' }).click()
  await expect(page.getByText('用户手工输入（未回测验证）')).toBeVisible()
  expect(saved.acknowledge_research_only).toBe(true)
  expect(saved.parameters.target_pct).toBe(0.18)
})
