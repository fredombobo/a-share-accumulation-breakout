import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, type BacktestCatalog, type BacktestPreview } from '../src/api/client'
import ProfessionalBacktest from '../src/pages/ProfessionalBacktest'

const catalog: BacktestCatalog = {
  version: 'test-v1',
  max_combinations: 512,
  parameters: [
    {
      key: 'box_max_days', title: '横盘最长天数', group: 'signal', value_type: 'integer',
      minimum: 40, maximum: 240, default: { mode: 'range', start: 60, stop: 200, step: 20 },
      description: '步进搜索横盘长度',
    },
    {
      key: 'breakout_vol_ratio', title: '突破量比', group: 'signal', value_type: 'number',
      minimum: 1, maximum: 5, default: { mode: 'values', values: [1.4, 1.6, 1.8] },
      description: '突破量相对箱体均量',
    },
  ],
  conditions: [],
  research_boundary: 'EXPLORATORY_ONLY',
  paper_trading_enabled: false,
  live_trading_enabled: false,
}

const preview: BacktestPreview = {
  can_run: true,
  estimated_work: { combinations: 24, stocks: 30, sample_step: 10, note: '后台运行' },
  prepared: {
    contract_version: 'test-v1', strategy: 'A', sample_step: 10, max_codes: 600,
    parameters: Object.fromEntries(catalog.parameters.map((item) => [item.key, item.default])),
    universe: {
      industries: [], codes: Array.from({ length: 30 }, (_, index) => `${String(index).padStart(6, '0')}.SZ`),
      source: 'CURRENT_ALL', count: 30, sha256: 'universe',
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
    },
    conditions: [],
    windows: { mode: 'auto', is: ['20230101', '20241231'], oos: ['20250101', '20260828'], wf: [], n_dates: 800 },
    parameter_space: { count: 24, sha256: 'grid', horizon: 280, signal_group_count: 24, exit_group_count: 1, invalid_signal_combinations: 0 },
    input_hash: 'input-hash',
  },
}

afterEach(() => vi.restoreAllMocks())

describe('专业回测工作台', () => {
  it('使用版本化默认参数先预览，不静默启动任务', async () => {
    vi.spyOn(api, 'backtestCatalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'backtestUniverse').mockResolvedValue({
      classification_mode: 'CURRENT_CLASSIFICATION_FROZEN_UNIVERSE', classification_note: '当前分类只用于选择',
      industries: [{ name: '半导体', count: 30 }], stocks: [], stock_count: 30,
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null })
    const previewSpy = vi.spyOn(api, 'backtestPreview').mockResolvedValue(preview)
    const runSpy = vi.spyOn(api, 'backtestRun')

    render(<ProfessionalBacktest />)
    await screen.findByRole('heading', { name: '多参数专业回测' })
    expect(screen.getByLabelText('交易日采样间隔')).toHaveValue(10)
    expect(screen.getByText(/每隔 N 个交易日生成一个研究决策截面/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '检查参数空间' }))

    await waitFor(() => expect(previewSpy).toHaveBeenCalledOnce())
    expect(previewSpy.mock.calls[0][0].parameters.box_max_days).toEqual({ mode: 'range', start: 60, stop: 200, step: 20 })
    expect(await screen.findByText('24')).toBeVisible()
    expect(runSpy).not.toHaveBeenCalled()
  })
})
