import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import BacktestMarketComparison from '../src/components/BacktestMarketComparison'
import type { BacktestMarketComparison as Comparison } from '../src/api/client'

vi.mock('../src/components/EChart', () => ({ default: ({ option }: { option: unknown }) => <div data-testid="chart">{JSON.stringify(option)}</div> }))
vi.mock('../src/theme/ThemeContext', () => ({ useChartColors: () => ({ text: '#111', subtext: '#888', axis: '#aaa', split: '#eee', accent: '#456', warn: '#987' }) }))

describe('同快照大盘对照', () => {
  it('不会给历史缺失报告补造跑赢结论', () => {
    render(<BacktestMarketComparison />)
    expect(screen.getByRole('status').textContent).toContain('不能据此宣称跑赢大盘')
    expect(screen.queryByTestId('chart')).toBeNull()
  })
  it('显示真实差值、费用口径和可切换图表', () => {
    const row = { start: '20250102', end: '20251231', anchor_date: '20241231', sessions: 240,
      strategy_return: .07, benchmark_return: .12, excess_return: -.05, strategy_max_drawdown: .08,
      benchmark_max_drawdown: .15, stress_excess_return: -.08,
      curve: [{ trade_date: '20251231', strategy_nav: 1.07, benchmark_nav: 1.12, excess_return: -.05, strategy_drawdown: .08, benchmark_drawdown: .15 }] }
    const data: Comparison = { version: 'v1', benchmark_code: '000300.SH', benchmark_name: '沪深300',
      status: 'COMPLETE', sha256: 'hash', notice: '价格指数不含分红；不是 alpha', oos: row,
      is: { ...row, strategy_return: .20, excess_return: .08 }, wf: [] }
    render(<BacktestMarketComparison comparison={data} />)
    expect(screen.getByText(/-5.00 个百分点/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '样本外' }).className).toBe('seg-item on')
    expect(screen.getByRole('table').className).toBe('data')
    expect(JSON.parse(screen.getByTestId('chart').textContent || '{}').animation).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '累计收益差' }))
    expect(screen.getByRole('button', { name: '累计收益差' }).className).toBe('seg-item on')
    expect(screen.getByTestId('chart').textContent).toContain('策略减沪深300')
    fireEvent.click(screen.getByRole('button', { name: '样本内' }))
    expect(screen.getByText(/20.00%/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '回撤对照' }))
    expect(screen.getByTestId('chart').textContent).toContain('回撤 %')
  })
})
