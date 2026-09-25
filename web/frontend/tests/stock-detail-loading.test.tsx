import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type StockDetail as Detail, type StockFlowResp } from '../src/api/client'
import StockDetail from '../src/pages/StockDetail'

const route = vi.hoisted(() => ({ tsCode: '000001.SZ', runId: '' }))
vi.mock('react-router', () => ({ useParams: () => ({ tsCode: route.tsCode }), useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams(route.runId ? { run_id: route.runId } : {})] }))
vi.mock('../src/theme/ThemeContext', () => ({ useChartColors: () => ({ up: '#c34', down: '#297', text: '#123', subtext: '#789', axis: '#ccc', split: '#eee', accent: '#36a', accent2: '#38a', warn: '#a80' }) }))
vi.mock('../src/components/EChart', () => ({ default: () => <div data-testid="price-chart" /> }))
vi.mock('../src/components/FundFlowChart', () => ({ default: () => <div data-testid="flow-chart" /> }))
vi.mock('../src/components/AIReviewPanel', () => ({ default: ({ tsCode }: { tsCode: string }) => <div data-testid="ai-review" data-code={tsCode} /> }))

function detail(tsCode: string, name: string): Detail {
  return { ts_code: tsCode, name, industry: '银行', area: '深圳', list_date: '19910101', kline: [],
    signal: { box_high: null, box_low: null, box_days: null, box_amp: null, breakout_date: null,
      breakout_vol_ratio: null, breakout_pct_chg: null, vol_shrink_ratio: null, ma5: null, ma10: null, ma20: null, reasons: [] },
    fundamentals: { pe: 8, pb: 1, total_mv_wan: 500000, circ_mv_wan: 400000, turnover_rate: 1, volume_ratio: 1.2, close: 10 },
    fund_flow: { net_wan: 100, score: 50, ratio_pct: 1, days: 5 }, fina: [], as_of: '20260911',
  }
}

function flow(tsCode = '000001.SZ', days = 20): StockFlowResp {
  return { ts_code: tsCode, name: '', industry: '银行', days, stock_flow: [], sector_flow: { dates: [], net_wan: [] }, as_of: '20260911' }
}

beforeEach(() => {
  route.tsCode = '000001.SZ'
  route.runId = ''
  vi.spyOn(api, 'stockFlow').mockImplementation((code, days) => Promise.resolve(flow(code, days)))
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('StockDetail request ownership', () => {
  it('历史详情沿用扫描日期，不混入当前资金图和即时 AI 评测', async () => {
    route.runId = 'scan-history'
    const stock = vi.spyOn(api, 'stock').mockResolvedValue({ ...detail('000001.SZ', '历史股票'), historical_view: true, selection_status: 'HISTORICAL_CANDIDATE', scan_as_of: '20260901', fund_flow: { net_wan: null, score: null, ratio_pct: null, days: 0 } } as Detail)
    render(<StockDetail />)
    expect(await screen.findByText('历史股票')).toBeVisible()
    expect(stock).toHaveBeenCalledWith('000001.SZ', undefined, 'scan-history')
    expect(api.stockFlow).not.toHaveBeenCalled()
    expect(screen.queryByTestId('ai-review')).not.toBeInTheDocument()
    expect(screen.queryByTestId('flow-chart')).not.toBeInTheDocument()
  })
  it('changes the fund-flow period without refetching the stock details', async () => {
    const stock = vi.spyOn(api, 'stock').mockResolvedValue(detail('000001.SZ', '当前股票'))
    render(<StockDetail />)
    expect(await screen.findByText('当前股票')).toBeInTheDocument()
    expect(stock).toHaveBeenCalledTimes(1)
    expect(api.stockFlow).toHaveBeenCalledWith('000001.SZ', 20)
    fireEvent.click(screen.getByRole('button', { name: '5 日' }))
    await waitFor(() => expect(api.stockFlow).toHaveBeenLastCalledWith('000001.SZ', 5))
    expect(screen.getByText('当前股票')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '10 日' }))
    await waitFor(() => expect(api.stockFlow).toHaveBeenLastCalledWith('000001.SZ', 10))
    expect(api.stockFlow).toHaveBeenCalledTimes(3)
    expect(stock).toHaveBeenCalledTimes(1)
  })

  it.each(['success', 'failure'] as const)('ignores an old stock %s after changing code', async (outcome) => {
    let resolveOld!: (value: Detail) => void
    let rejectOld!: (error: Error) => void
    const pendingOld = new Promise<Detail>((resolve, reject) => { resolveOld = resolve; rejectOld = reject })
    const stock = vi.spyOn(api, 'stock').mockImplementation((code) => code === '000001.SZ'
      ? pendingOld : Promise.resolve(detail('600036.SH', '新股票')))
    const view = render(<StockDetail />)
    expect(screen.getByText('正在整理个股证据')).toBeInTheDocument()
    route.tsCode = '600036.SH'
    view.rerender(<StockDetail />)
    expect(await screen.findByText('新股票')).toBeInTheDocument()
    await act(async () => {
      if (outcome === 'success') resolveOld(detail('000001.SZ', '旧股票'))
      else rejectOld(new Error('old request failed'))
      await pendingOld.catch(() => undefined)
    })
    expect(screen.getByText('新股票')).toBeInTheDocument()
    expect(screen.queryByText('旧股票')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByTestId('ai-review')).toHaveAttribute('data-code', '600036.SH')
    expect(stock).toHaveBeenNthCalledWith(1, '000001.SZ')
    expect(stock).toHaveBeenNthCalledWith(2, '600036.SH')
  })
})
