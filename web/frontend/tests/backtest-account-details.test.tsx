import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BacktestResult } from '../src/api/client'
import BacktestAccountDetails, { fenMoney, materialContributions } from '../src/components/BacktestAccountDetails'

vi.mock('../src/components/EChart', () => ({ default: () => <div data-testid="chart" /> }))

describe('账户复盘', () => {
  it('不为旧报告补造曲线，并解释旧零交易口径', () => {
    render(<BacktestAccountDetails result={{ wf: { wf_detail: [{ window: 'WF2', test_n: 0 }] } } as BacktestResult} />)
    expect(screen.getByText(/这份历史结果未保存/)).toBeVisible()
    expect(screen.queryByTestId('chart')).toBeNull()
    fireEvent.click(screen.getByText('逐窗样本诊断：为什么不足 30 笔？'))
    expect(screen.getAllByText(/不能把旧报告的 0/)).toHaveLength(2)
  })
  it('只渲染服务端账户路径和逐笔金额，切换区间不冒用另一条曲线', () => {
    const result = { wf: null, account_details: { oos: {
      initial_equity_fen: '10000000', final_equity_fen: '10001234', realized_pnl_fen: '1234', unrealized_pnl_fen: '0',
      reconciliation: 'EXACT_FEN', note: '真实账户明细', equity_curve: [{ trade_date: '20260831', equity_fen: '10001234' }],
      monthly: [{ month: '202608', net_return: '0.0001234', net_pnl_fen: '1234' }], stock_contribution: [], industry_contribution: [], exit_contribution: [], events: [],
    } } } as unknown as BacktestResult
    render(<BacktestAccountDetails result={result} />)
    expect(screen.getByText('100,000.00 → 100,012.34')).toBeVisible()
    expect(screen.getByText(/零分差异/)).toBeVisible()
    expect(screen.getAllByTestId('chart')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '样本内' }))
    expect(screen.queryByTestId('chart')).toBeNull()
  })
  it('分位金额显示不经过浮点舍入', () => {
    expect(fenMoney('-123401')).toBe('-1,234.01')
    expect(fenMoney('900719925474099301')).toBe('9,007,199,254,740,993.01')
  })
  it('贡献图按绝对影响排序，不让大量小盈利挤掉主要亏损', () => {
    const rows = Array.from({ length: 20 }, (_, i) => ({ name: `盈利${i}`, realized_pnl_fen: '100' }))
    rows.push({ name: '主要亏损', realized_pnl_fen: '-900719925474099301' })
    const visible = materialContributions(rows)
    expect(visible).toHaveLength(15)
    expect(visible[0].name).toBe('主要亏损')
    expect(rows.at(-1)?.name).toBe('主要亏损')
  })
})
