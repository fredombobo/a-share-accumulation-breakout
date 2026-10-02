import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BacktestAccountDetails, BacktestLeaderboardRow, BacktestResult } from '../src/api/client'
import BacktestResultCharts from '../src/components/BacktestResultCharts'

vi.mock('../src/components/EChart', () => ({
  default: ({ option, ariaLabel }: { option: unknown; ariaLabel?: string }) => <div role="img" aria-label={ariaLabel} data-testid="atlas-chart">{JSON.stringify(option)}</div>,
}))
vi.mock('../src/theme/ThemeContext', () => ({
  useChartColors: () => ({ text: '#17263b', subtext: '#657185', axis: '#ccd5df', split: '#e9edf2', accent: '#2861a5', accent2: '#528caa', up: '#c85151', down: '#31906c', warn: '#af8741', palette: ['#2861a5', '#528caa', '#af8741', '#31906c'] }),
}))

const parameter = (param_id: string, isReturn: number, oosReturn: number): BacktestLeaderboardRow => ({
  param_id, signal: {}, exit: {},
  is: { portfolio_total_return: isReturn, portfolio_max_drawdown: .02, net_n_trades: 10, net_profit_factor: 1.2 },
  oos: { portfolio_total_return: oosReturn, portfolio_max_drawdown: .03, net_n_trades: 2, net_profit_factor: null },
})
const account = (scope: 'is' | 'oos', overrides: Partial<BacktestAccountDetails> = {}): BacktestAccountDetails => {
  const date = scope === 'is' ? '20240202' : '20250203'
  return {
    version: 'account-v1', equity_sha256: 'fixture', initial_equity_fen: '10000', final_equity_fen: '10000',
    realized_pnl_fen: '0', unrealized_pnl_fen: '0', reconciliation: 'EXACT_FEN', note: '实际账户记录',
    equity_curve: [{ trade_date: date, cash_fen: '8000', market_value_fen: '2000', equity_fen: '10000', drawdown: 0 }],
    monthly: [{ month: date.slice(0, 6), net_return: '0', net_pnl_fen: '0' }],
    events: [
      { event: 'ENTRY_FILLED', trade_date: date, ts_code: '600001.SH', qty: 100, fee_breakdown: { commission_fen: '100', stamp_tax_fen: '0', other_fee_fen: '5', slippage_fen: '30' } },
      { event: 'EXIT_FILLED', trade_date: date, ts_code: '600001.SH', qty: 100, realized_pnl_fen: '0', fee_breakdown: { commission_fen: '100', stamp_tax_fen: '20', other_fee_fen: '5', slippage_fen: '70' } },
    ],
    stock_contribution: [], industry_contribution: [], exit_contribution: [], ...overrides,
  }
}
const result = (overrides: Partial<BacktestResult> = {}): BacktestResult => {
  const first = parameter('is-first', .3, -.05)
  return {
    verdict: 'INSUFFICIENT', verdict_label: '研究证据不足', candidate_eligible: false, can_claim_edge: false,
    request: { windows: { is: ['20240101', '20241231'], oos: ['20250101', '20251231'] } } as BacktestResult['request'],
    selected: first, leaderboard: [first], wf: null, baselines: null, cost_stress: null, warnings: [],
    account_details: { is: account('is'), oos: account('oos') }, ...overrides,
  }
}
const selectView = (name: string) => fireEvent.click(screen.getByRole('tab', { name: new RegExp(name) }))
const chart = (label: string) => JSON.parse(screen.getByRole('img', { name: label }).textContent || '{}')

describe('result atlas UI evidence boundaries', () => {
  it('keeps a missing OOS account empty and switches only to the actual IS account', () => {
    render(<BacktestResultCharts result={result({ account_details: { is: account('is') } })} />)
    const nav = screen.getByRole('region', { name: '净值与回撤' })
    expect(within(nav).queryByRole('img')).toBeNull()
    expect(within(nav).getByText('这份报告没有保存所选区间的有效权益路径。')).toBeTruthy()
    selectView('交易画像')
    expect(within(screen.getByRole('region', { name: '资金使用轨迹' })).queryByRole('img')).toBeNull()
    expect(within(screen.getByRole('region', { name: '实际费用构成' })).getByText('费用记录不完整，暂不汇总或计算占比。')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'IS 样本内' }))
    expect(chart('样本内股票仓位与现金比例走势').xAxis.data).toEqual(['2024-02-02'])
    selectView('业绩与风险')
    expect(chart('样本内策略净值与回撤').series[0].data).toEqual([1])
    expect(chart('样本内策略净值与回撤').series[0].connectNulls).toBe(false)
    expect(screen.getByRole('cell', { name: '2024年2月：0.00%' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '2024年1月：未记录' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'OOS 样本外' }))
    expect(within(screen.getByRole('region', { name: '净值与回撤' })).queryByRole('img')).toBeNull()
    expect(screen.queryByRole('table', { name: '样本内月度收益热力表' })).toBeNull()
  })

  it('separates recorded cash fees from slippage and labels sell points as events', () => {
    render(<BacktestResultCharts result={result()} />)
    selectView('交易画像')
    const fees = screen.getByRole('region', { name: '实际费用构成' })
    const option = chart('样本外佣金、印花税与其他现金费用构成')
    expect(option.series[0].type).toBe('pie')
    expect(option.series[0].data.map((row: { name: string; value: number }) => [row.name, row.value])).toEqual([
      ['佣金', 2], ['印花税', .2], ['其他费用', .1],
    ])
    expect(option.graphic[0].style.text).toBe('2.30')
    expect(option.series[0].data.some((row: { name: string }) => row.name.includes('滑点'))).toBe(false)
    expect(within(fees).getByText('成交价中的滑点').parentElement?.textContent).toContain('1.00')
    expect(within(fees).getByText(/不再加入现金费用重复扣除/)).toBeTruthy()
    const exits = screen.getByRole('region', { name: '逐次卖出损益' })
    expect(within(exits).getByText(/不等同于独立完整交易/)).toBeTruthy()
    expect(chart('样本外实际卖出事件净损益，单位元').series[0].data[0].value).toBe(0)
  })

  it('does not draw a fee pie with incomplete coverage but lists individually complete fee categories', () => {
    const missingOther = account('oos')
    delete missingOther.events[1].fee_breakdown!.other_fee_fen
    delete missingOther.events[1].fee_breakdown!.slippage_fen
    render(<BacktestResultCharts result={result({ account_details: { oos: missingOther } })} />)
    selectView('交易画像')
    const fees = screen.getByRole('region', { name: '实际费用构成' })
    expect(within(fees).queryByRole('img')).toBeNull()
    expect(within(fees).getByText('费用记录不完整，暂不汇总或计算占比。')).toBeTruthy()
    expect(within(fees).getByText('佣金')).toBeTruthy()
    expect(within(fees).getByText('印花税')).toBeTruthy()
    expect(within(fees).queryByText('其他费用')).toBeNull()
    expect(within(fees).getByText('成交价中的滑点').parentElement?.textContent).toContain('—')
    expect(within(fees).queryByText('已记录现金费用为零')).toBeNull()
    expect(screen.getByText(/数据完整性说明/)).toBeTruthy()
  })

  it('shows an actual recorded zero fee amount rather than a fabricated pie', () => {
    render(<BacktestResultCharts result={result({ account_details: { oos: account('oos', { events: [] }) } })} />)
    selectView('交易画像')
    const fees = screen.getByRole('region', { name: '实际费用构成' })
    expect(within(fees).queryByRole('img')).toBeNull()
    expect(within(fees).getByText('已记录现金费用为零')).toBeTruthy()
  })

  it('retains every WF window and its zero/nonzero sample counts when all PF values are null', () => {
    render(<BacktestResultCharts result={result({ wf: { wf_detail: [
      { window: 'WF-empty', train_pf: null, test_pf: null, train_n: 17, test_n: 0 },
      { window: 'WF-no-losses', train_pf: null, test_pf: null, train_n: 1, test_n: 2 },
    ] } })} />)
    selectView('参数稳健性')
    const panel = screen.getByRole('region', { name: 'WF 窗口稳定性' })
    expect(within(panel).queryByRole('img')).toBeNull()
    const rows = within(panel).getAllByRole('row').slice(1)
    expect(rows.map(row => within(row).getAllByRole('cell').map(cell => cell.textContent))).toEqual([
      ['WF-empty', '— / 17', '— / 0'], ['WF-no-losses', '— / 1', '— / 2'],
    ])
    expect(within(panel).getByText(/不能按 PF = 0 处理/)).toBeTruthy()
  })

  it('keeps the matrix and scatter labels in original IS order despite a different OOS ranking', () => {
    const ranked = [parameter('original-1', .3, -.1), parameter('original-2', .2, .8), parameter('original-3', .1, .2)]
    render(<BacktestResultCharts result={result({ leaderboard: ranked, selected: ranked[0] })} />)
    selectView('参数稳健性')
    const matrix = screen.getByRole('region', { name: '历史名义参数表现矩阵' })
    const rows = within(matrix).getAllByRole('row').slice(1)
    expect(rows.map(row => row.querySelector('code')?.getAttribute('title'))).toEqual(['original-1', 'original-2', 'original-3'])
    expect(rows.map(row => within(row).getAllByRole('cell')[2].textContent)).toEqual(['-10.00%', '+80.00%', '+20.00%'])
    expect(chart('参数的样本外风险收益散点图').series[0].data.map((row: { value: number[] }) => row.value[3])).toEqual([1, 2, 3])
    fireEvent.click(screen.getByRole('button', { name: 'IS 样本内' }))
    expect(within(matrix).getAllByRole('row').slice(1).map(row => row.querySelector('code')?.getAttribute('title'))).toEqual(['original-1', 'original-2', 'original-3'])
  })

  it('uses verified independent-path order only when path evidence is complete', () => {
    const independent = [parameter('path-first', .3, -.2), parameter('path-second', .1, .9)]
    const input = result({ independent_leaderboard: independent, path_analysis: { method: 'equity', evidence_complete: true,
      nominal_combinations: 3, independent_is_paths: 2, independent_oos_paths: 2, independent_joint_paths: 2, duplicate_group_count: 1,
    } })
    const { rerender } = render(<BacktestResultCharts result={input} />)
    selectView('参数稳健性')
    expect(within(screen.getByRole('region', { name: '独立路径表现矩阵' })).getAllByRole('row').slice(1).map(row => row.querySelector('code')?.getAttribute('title'))).toEqual(['path-first', 'path-second'])
    rerender(<BacktestResultCharts result={{ ...input, path_analysis: { ...input.path_analysis!, evidence_complete: false } }} />)
    expect(screen.getByRole('region', { name: '历史名义参数表现矩阵' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: '独立路径表现矩阵' })).toBeNull()
  })
})
