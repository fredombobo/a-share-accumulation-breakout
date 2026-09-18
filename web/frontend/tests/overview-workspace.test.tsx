import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type OverviewItem, type OverviewResp, type StrategyProfileState } from '../src/api/client'
import { loadOverviewCache, saveOverviewCache } from '../src/scanCache'
import Overview from '../src/pages/Overview'

vi.mock('react-router', async () => {
  const { useState } = await import('react')
  return { useNavigate: () => vi.fn(), useSearchParams: () => {
    const [params, setParams] = useState(new URLSearchParams())
    return [params, (next: Record<string, string>) => setParams(new URLSearchParams(next))]
  } }
})
vi.mock('../src/theme/ThemeContext', () => ({ useChartColors: () => ({ up: '#d35', down: '#297', warn: '#b80', accent2: '#48a' }) }))
vi.mock('../src/components/EChart', () => ({ default: () => <div data-testid="candidate-chart" /> }))
vi.mock('../src/components/MoneyHeatmap', () => ({ default: () => <div data-testid="money-heatmap" /> }))
vi.mock('../src/components/SectorFlowPanel', () => ({ default: () => <div /> }))
vi.mock('../src/components/GlobalRunProgress', () => ({ RUN_TASK_EVENT: 'run-task' }))

function item(name: string, pool: 'A' | 'B' = 'A'): OverviewItem {
  return { ts_code: `${name}.SZ`, code: '000001', name, price: 10, industry: '测试行业', mv_yi: 40,
    pe: 20, pb: 2, turnover: 1, score: 72, box_days: 40, box_amp: 15.2, vol_ratio: 1.8,
    fund_net_wan: 1200, fund_ratio: 1, breakout_date: '20260911', reasons: '[池A|strict|] 箱体完整；放量突破',
    pool, tier: pool === 'A' ? 'strict' : 'relaxed', tradeable: pool === 'A',
    trade: { entry_ref: 10, stop_loss: 9.3, target_1: 11.2, target_2: null, position_pct: 15, max_hold_days: 15, tradeable: true, pool },
  }
}

function result(pool: 'A' | 'B' | 'ALL', names: string[] = []): OverviewResp {
  return { pool, as_of: '20260911', count: names.length, items: names.map((name) => item(name, pool === 'B' ? 'B' : 'A')) }
}

function profile(): StrategyProfileState {
  return { active: { profile_id: 'fixture', name: 'fixture', version: '1', schema_version: 4, is_default: true,
    status: 'active', storage_status: 'built_in', config_hash: 'fixture', required_scan_days: 160,
    entry: { box_min_days: 20, box_max_days: 125, box_max_amp: .26, breakout_vol_ratio: 1.6,
      breakout_chg_min: .02, breakout_chg_max: .095, breakout_vs_recent_vol_ratio: 1.3,
      breakout_window_days: 5, require_structure: true },
    exit_reference: { vol_ratio_min: 1.5, stop_pct: .07, target_pct: .12, max_hold_days: 30, exit_window: 10, strong_reset: 3 },
    source: { kind: 'BUILT_IN' }, notes: [] }, history: [], live_trading_enabled: false,
    boundary: { scope: 'DAILY_A_POOL_TECHNICAL_ENTRY', manual_activation_required: true,
      automatic_promotion: false, b_pool_uses_profile: false, daily_extra_gates: [], notice: '技术筛选' },
  }
}

beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', time: '', as_of: '20260911' })
  vi.spyOn(api, 'setupStatus').mockResolvedValue({} as never)
  vi.spyOn(api, 'today').mockResolvedValue(null as never)
  vi.spyOn(api, 'classifications').mockResolvedValue(null as never)
  vi.spyOn(api, 'moneyHeatmap').mockRejectedValue(new Error('fixture unavailable'))
  vi.spyOn(api, 'sectorFlow').mockRejectedValue(new Error('fixture unavailable'))
  vi.spyOn(api, 'scanStatus').mockResolvedValue({ status: 'idle', progress: 0 } as never)
  vi.spyOn(api, 'backtestProfile').mockResolvedValue(profile())
  vi.spyOn(api, 'scanRuns').mockResolvedValue({ runs: [] })
})

afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear() })

describe('daily workspace result identity and controls', () => {
  it.each([
    { flows: [null, null], amount: '缺少数据', coverage: '0/2' },
    { flows: [0, null], amount: '0.00 亿', coverage: '1/2' },
    { flows: [-1200, null], amount: '-0.12 亿', coverage: '1/2' },
  ])('distinguishes missing candidate funds from actual zero ($coverage, $amount)', async ({ flows, amount, coverage }) => {
    const response = result('A', ['候选甲', '候选乙'])
    response.items.forEach((entry, index) => { entry.fund_net_wan = flows[index] })
    vi.spyOn(api, 'overview').mockResolvedValue(response)
    render(<Overview />)
    const total = await screen.findByText(/有数据候选资金净额/)
    expect(total).toHaveTextContent(amount)
    expect(total).toHaveTextContent(`已取得 ${coverage} 只`)
    if (amount === '缺少数据') expect(total).not.toHaveTextContent('0.00 亿')
  })

  it('does not restore another pool after a request fails', async () => {
    saveOverviewCache('B', result('B', ['历史B']))
    sessionStorage.setItem('ab_screener_pool_v1', 'A')
    vi.spyOn(api, 'overview').mockRejectedValue(new Error('network down'))
    render(<Overview />)
    expect(await screen.findByRole('alert')).toHaveTextContent('network down')
    expect(screen.queryByText('历史B')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'A · 严格候选' })).toHaveAttribute('aria-selected', 'true')
  })

  it('labels matching cached results as historical without hiding the request error', async () => {
    saveOverviewCache('A', { ...result('A', ['历史A']), as_of: '20260904' })
    vi.spyOn(api, 'overview').mockRejectedValue(new Error('server unavailable'))
    render(<Overview />)
    expect(await screen.findByRole('alert')).toHaveTextContent('server unavailable')
    expect(screen.getByText('历史A')).toBeInTheDocument()
    expect(screen.getByText('历史缓存 · 待核对')).toBeInTheDocument()
    expect(screen.getByText('2026.09.04')).toBeInTheDocument()
  })

  it('accepts and caches a successful empty result so navigation cannot resurrect old candidates', async () => {
    saveOverviewCache('A', result('A', ['旧候选']))
    vi.spyOn(api, 'overview').mockResolvedValue({ ...result('A'), empty_reason: '本次扫描没有符合条件的标的' })
    const first = render(<Overview />)
    expect(await screen.findByText('本次扫描没有符合条件的标的')).toBeInTheDocument()
    expect(screen.queryByText('旧候选')).not.toBeInTheDocument()
    expect(loadOverviewCache()?.data.items).toEqual([])
    first.unmount()
    render(<Overview />)
    expect(screen.queryByText('旧候选')).not.toBeInTheDocument()
    expect(await screen.findByText('本次扫描没有符合条件的标的')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows timeout errors even when no cache exists', async () => {
    vi.spyOn(api, 'overview').mockRejectedValue(new DOMException('timed out', 'AbortError'))
    render(<Overview />)
    expect(await screen.findByRole('alert')).toHaveTextContent('读取结果超时')
    expect(screen.getByRole('button', { name: '重试' })).toBeEnabled()
  })

  it('ignores a late response from the previous pool', async () => {
    let resolveA!: (value: OverviewResp) => void
    const pendingA = new Promise<OverviewResp>((resolve) => { resolveA = resolve })
    vi.spyOn(api, 'overview').mockImplementation((pool) => pool === 'A' ? pendingA : Promise.resolve(result('B', ['当前B'])))
    render(<Overview />)
    fireEvent.click(screen.getByRole('tab', { name: 'B · 观察名单' }))
    expect(await screen.findByText('当前B')).toBeInTheDocument()
    resolveA(result('A', ['过时A']))
    await waitFor(() => expect(loadOverviewCache()?.pool).toBe('B'))
    expect(screen.queryByText('过时A')).not.toBeInTheDocument()
  })

  it('submits exactly nine entry fields with the editor version and puts evidence before market context', async () => {
    vi.spyOn(api, 'overview').mockResolvedValue(result('A', ['候选甲']))
    const newest = profile()
    newest.active.exit_reference = { vol_ratio_min: 2.1, stop_pct: .06, target_pct: .16, max_hold_days: 45, exit_window: 14, strong_reset: 5 }
    vi.mocked(api.backtestProfile).mockResolvedValueOnce(profile()).mockResolvedValue(newest)
    const save = vi.spyOn(api, 'saveEntryProfile').mockResolvedValue(newest)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<Overview />)
    expect(await screen.findByText('候选甲')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /编辑筛选条件/ }))
    const input = await screen.findByLabelText('横盘最短（交易日）')
    for (const label of ['止损（%）', '止盈（%）', '最长持有（交易日）', '建仓量 / 前 5 日均量', '二次出货观察窗（日）', '强势日清零根数']) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    }
    expect(screen.queryByText('建议仓位')).not.toBeInTheDocument()
    expect(screen.queryByText('9.3')).not.toBeInTheDocument()
    expect(screen.getByText('横盘天数')).toBeInTheDocument()
    const evidence = screen.getByRole('article', { name: '候选甲候选证据' })
    const market = screen.getByRole('heading', { name: '资金环境' })
    expect(evidence.compareDocumentPosition(market) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.change(input, { target: { value: '35' } })
    fireEvent.click(screen.getByRole('button', { name: '保存筛选条件' }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0][0]).toEqual({ ...profile().active.entry, box_min_days: 35 })
    expect(Object.keys(save.mock.calls[0][0])).toHaveLength(9)
    expect(save.mock.calls[0][1]).toBe('fixture')
    expect(api.backtestProfile).toHaveBeenCalledTimes(1)
  })

  it('loads explicit history separately and never replaces the latest cache with history', async () => {
    const current = { ...result('A', ['当前甲']), publication: { run_id: 'current' }, is_current: true }
    const past = { ...result('A', ['历史甲']), publication: { run_id: 'old', entry_hash: 'frozen-old' }, view_state: 'HISTORICAL' as const }
    vi.mocked(api.scanRuns).mockResolvedValue({ runs: [{ run_id: 'old', as_of: '20260904', created_at: '2026-09-04T18:00:00', status: 'SUCCEEDED', config_hash: 'old' }] })
    vi.spyOn(api, 'overview').mockImplementation((_pool, _opts, runId) => Promise.resolve(runId ? past : current))
    render(<Overview />)
    expect(await screen.findByText('当前甲')).toBeVisible()
    fireEvent.change(screen.getByRole('combobox', { name: '查看扫描记录' }), { target: { value: 'old' } })
    expect(await screen.findByText('历史甲')).toBeVisible()
    expect(screen.queryByText('当前甲')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /历史结果/ })).toBeVisible()
    expect(loadOverviewCache()?.data.publication?.run_id).toBe('current')
    fireEvent.click(screen.getByRole('button', { name: '返回最新' }))
    expect(await screen.findByText('当前甲')).toBeVisible()
  })

  it('shows an entry version conflict without retrying or silently replacing the draft', async () => {
    vi.spyOn(api, 'overview').mockResolvedValue(result('A'))
    const save = vi.spyOn(api, 'saveEntryProfile').mockRejectedValue(new Error('PROFILE_CHANGED：档案已更新'))
    render(<Overview />)
    fireEvent.click(await screen.findByRole('button', { name: /编辑筛选条件/ }))
    const input = await screen.findByLabelText('横盘最短（交易日）')
    fireEvent.change(input, { target: { value: '35' } })
    fireEvent.click(screen.getByRole('button', { name: '保存筛选条件' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('PROFILE_CHANGED')
    expect(input).toHaveValue(35)
    expect(save).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '重新载入条件' })).toBeVisible()
  })
})
