import { describe, expect, it } from 'vitest'
import type { BacktestAccountDetails, BacktestResult } from '../src/api/client'
import { buildAtlasData } from '../src/components/backtestAtlasData'

type Point = BacktestAccountDetails['equity_curve'][number]
type Event = BacktestAccountDetails['events'][number]
const point = (trade_date = '20250102', changes: Partial<Point> = {}): Point => ({ trade_date, equity_fen: '10000', cash_fen: '8000', market_value_fen: '2000', drawdown: 0, ...changes })
const fill = (event = 'EXIT_FILLED', changes: Partial<Event> = {}): Event => ({
  event, ts_code: '600001.SH', trade_date: '20250102', qty: 100, realized_pnl_fen: '125',
  fee_breakdown: { commission_fen: '100', stamp_tax_fen: '20', other_fee_fen: '5', slippage_fen: '50' }, ...changes,
})
const account = (changes: Partial<BacktestAccountDetails> = {}): BacktestAccountDetails => ({
  version: 'account-v1', equity_sha256: 'exact', initial_equity_fen: '10000', final_equity_fen: '10000',
  realized_pnl_fen: '0', unrealized_pnl_fen: '0', reconciliation: 'EXACT_FEN', note: '实际账户',
  equity_curve: [point()], monthly: [{ month: '202501', net_return: '0', net_pnl_fen: '0' }],
  events: [], stock_contribution: [], industry_contribution: [], exit_contribution: [], ...changes,
})
const result = (oos?: BacktestAccountDetails, is?: BacktestAccountDetails): BacktestResult => ({
  account_details: { oos, is }, request: { windows: { is: ['20240101', '20241231'], oos: ['20250101', '20251231'] } },
  selected: { oos: { net_avg_return: 999, portfolio_total_return: 88 } },
} as BacktestResult)

describe('account evidence atlas adaptation', () => {
  it('keeps actual zero values and a single observed date without adding an initial anchor', () => {
    const data = buildAtlasData(result(account({ equity_curve: [point('20250102', { cash_fen: '10000', market_value_fen: '0' })] })), 'oos')
    expect(data.curve).toEqual([{ date: '20250102', nav: 1, drawdownPct: 0, exposurePct: 0, cashPct: 100 }])
    expect(data.months).toEqual([{ month: '202501', returnPct: 0 }])
    expect(data.metrics).toEqual({ curveDays: 1, exposureDays: 1, averageExposurePct: 0, investedDays: 0, winningMonths: 0, availableMonths: 1, underwaterDays: 0, worstDrawdownPct: 0 })
    expect(data.cashFees).toEqual({ fen: '0', yuan: 0 })
    expect(data.slippage).toEqual({ fen: '0', yuan: 0 })
    expect(data.events.every(row => row.count === 0)).toBe(true)
    expect(data.errors).toEqual([])
  })

  it('computes normalized equity and exposure only from account balances', () => {
    const data = buildAtlasData(result(account({ equity_curve: [
      point('20250103', { equity_fen: '9000', cash_fen: '8100', market_value_fen: '900', drawdown: .1 }),
      point('20250102'),
    ], monthly: [{ month: '202501', net_return: '.05', net_pnl_fen: '500' }] })), 'oos')
    expect(data.curve.map(row => row.nav)).toEqual([1, .9])
    expect(data.curve[1]).toMatchObject({ exposurePct: 10, cashPct: 90, drawdownPct: 10 })
    expect(data.metrics).toMatchObject({ averageExposurePct: 15, investedDays: 2, winningMonths: 1, underwaterDays: 1, worstDrawdownPct: 10 })
    expect(JSON.stringify(data)).not.toContain('999')
  })

  it('does not borrow account records or legacy averages from another scope', () => {
    const is = account({ equity_curve: [point('20240202', { equity_fen: '20000', cash_fen: '16000', market_value_fen: '4000' })], monthly: [{ month: '202402', net_return: '1', net_pnl_fen: '10000' }], events: [fill('EXIT_FILLED', { trade_date: '20240202' })] })
    const data = buildAtlasData(result(undefined, is), 'oos')
    expect(data.availability.account).toBe(false)
    expect(data.curve).toEqual([])
    expect(data.months).toEqual([])
    expect(data.events).toEqual([])
    expect(data.cashFees).toEqual({ fen: null, yuan: null })
    expect(data.metrics.averageExposurePct).toBeNull()
    expect(buildAtlasData(result(undefined, is), 'is').curve[0].nav).toBe(2)
  })

  it('rejects records outside the selected window instead of mixing sample-in and sample-out', () => {
    const data = buildAtlasData(result(account({ equity_curve: [point('20241231'), point()], monthly: [
      { month: '202412', net_return: '1', net_pnl_fen: '10000' }, { month: '202501', net_return: '0', net_pnl_fen: '0' },
    ], events: [fill('EXIT_FILLED', { trade_date: '20241231' }), fill()] })), 'oos')
    expect(data.curve).toHaveLength(1)
    expect(data.months).toHaveLength(1)
    expect(data.exits).toHaveLength(1)
    expect(data.cashFees.fen).toBe('125')
    expect(data.errors.filter(row => row.code === 'OUTSIDE_SCOPE')).toHaveLength(3)
  })

  it('leaves missing daily values as null breaks and does not infer drawdown', () => {
    const data = buildAtlasData(result(account({ equity_curve: [point(),
      { trade_date: '20250103' } as Point,
      point('20250106', { drawdown: Number.NaN }),
    ] })), 'oos')
    expect(data.curve[1]).toEqual({ date: '20250103', nav: null, drawdownPct: null, exposurePct: null, cashPct: null })
    expect(data.curve[2].drawdownPct).toBeNull()
    expect(data.metrics.curveDays).toBe(2)
    expect(data.metrics.exposureDays).toBe(2)
    expect(data.errors.some(row => row.code === 'AMOUNT_INVALID')).toBe(true)
  })

  it('does not turn absent arrays into recorded zero costs or months', () => {
    const data = buildAtlasData(result(account({ equity_curve: undefined, monthly: undefined, events: undefined } as unknown as Partial<BacktestAccountDetails>)), 'oos')
    expect(data.availability).toMatchObject({ curve: false, months: false, events: false })
    expect(data.fees).toEqual([])
    expect(data.cashFees.yuan).toBeNull()
    expect(data.slippage.yuan).toBeNull()
    expect(data.metrics).toMatchObject({ investedDays: null, winningMonths: null, underwaterDays: null, worstDrawdownPct: null })
  })

  it('normalizes duplicate dates to one null point and duplicate months to a null month', () => {
    const data = buildAtlasData(result(account({ equity_curve: [point(), point('2025-01-02')], monthly: [
      { month: '202501', net_return: '.1', net_pnl_fen: '1000' },
      { month: '2025-01', net_return: '.2', net_pnl_fen: '2000' },
    ] })), 'oos')
    expect(data.curve).toEqual([{ date: '20250102', nav: null, drawdownPct: null, exposurePct: null, cashPct: null }])
    expect(data.months).toEqual([{ month: '202501', returnPct: null }])
    expect(data.metrics.availableMonths).toBe(0)
    expect(data.metrics.winningMonths).toBeNull()
  })

  it('keeps absent months absent for the UI to pad with null and never compounds across them', () => {
    const data = buildAtlasData(result(account({ monthly: [
      { month: '202501', net_return: '.1', net_pnl_fen: '1000' },
      { month: '202503', net_return: '-.1', net_pnl_fen: '-1000' },
    ] })), 'oos')
    expect(data.months).toEqual([{ month: '202501', returnPct: 10 }, { month: '202503', returnPct: -10 }])
    expect(data.metrics).toMatchObject({ availableMonths: 2, winningMonths: 1 })
  })

  it('rejects invalid dates and does not join the remaining curve across an unknown date', () => {
    const data = buildAtlasData(result(account({ equity_curve: [point(), point('20250230'), point('20250303')] })), 'oos')
    expect(data.curve).toEqual([])
    expect(data.availability.curve).toBe(false)
    expect(data.errors.some(row => row.code === 'DATE_INVALID')).toBe(true)
  })

  it('does not divide by zero and rejects unreconciled exposure while preserving actual nav', () => {
    const data = buildAtlasData(result(account({ initial_equity_fen: '0', equity_curve: [point('20250102', { cash_fen: '0', market_value_fen: '0', equity_fen: '0', drawdown: 1 })] })), 'oos')
    expect(data.curve[0]).toEqual({ date: '20250102', nav: null, drawdownPct: 100, exposurePct: null, cashPct: null })
    const mismatch = buildAtlasData(result(account({ equity_curve: [point('20250102', { cash_fen: '9999' })] })), 'oos')
    expect(mismatch.curve[0]).toMatchObject({ nav: 1, exposurePct: null, cashPct: null })
    expect(mismatch.errors.some(row => row.code === 'EQUITY_MISMATCH')).toBe(true)
  })

  it('calculates ratios in integer space without first rounding large fen balances', () => {
    const data = buildAtlasData(result(account({ initial_equity_fen: '100000000000000000001', equity_curve: [point('20250102', { equity_fen: '200000000000000000002', cash_fen: '100000000000000000001', market_value_fen: '100000000000000000001' })] })), 'oos')
    expect(data.curve[0]).toMatchObject({ nav: 2, exposurePct: 50, cashPct: 50 })
  })

  it.each(['', '1.5', 'NaN', 'Infinity', '0x10', 9007199254740992])('rejects non-integer or unsafe numeric fen %s', value => {
    const data = buildAtlasData(result(account({ equity_curve: [point('20250102', { equity_fen: value as string })] })), 'oos')
    expect(data.curve[0].nav).toBeNull()
    expect(data.errors.some(row => row.code === 'AMOUNT_INVALID')).toBe(true)
  })

  it('plots sell fills including zero PnL and partial fills, not entries, retries or completed trades', () => {
    const data = buildAtlasData(result(account({ events: [fill('ENTRY_FILLED'), fill('EXIT_FILLED', { qty: 50, realized_pnl_fen: '0' }),
      fill('EXIT_FILLED', { qty: 50, realized_pnl_fen: '-250' }), fill('ENTRY_REJECTED', { qty: 0 }), fill('EXIT_RETRY', { qty: 0 }),
    ] })), 'oos')
    expect(data.exits).toEqual([{ date: '20250102', code: '600001.SH', pnlYuan: 0 }, { date: '20250102', code: '600001.SH', pnlYuan: -2.5 }])
    expect(data.events).toEqual([{ label: '买入成交', count: 1 }, { label: '卖出成交', count: 2 }, { label: '买入未成交', count: 1 }, { label: '卖出顺延', count: 1 }])
    expect(data.availability.exits).toEqual({ expected: 2, available: 2, complete: true })
  })

  it('sums recorded actual fees once and reports slippage separately without recomputing amounts', () => {
    const data = buildAtlasData(result(account({ events: [fill('ENTRY_FILLED'), fill(),
      fill('ENTRY_REJECTED', { qty: 0, fee_breakdown: { commission_fen: '999999' } }),
      fill('EXIT_RETRY', { qty: 0, fee_breakdown: { commission_fen: '999999' } }),
    ] })), 'oos')
    expect(data.fees.map(row => [row.key, row.fen])).toEqual([['commission_fen', '200'], ['stamp_tax_fen', '40'], ['other_fee_fen', '10']])
    expect(data.cashFees).toEqual({ fen: '250', yuan: 2.5 })
    expect(data.slippage).toEqual({ fen: '100', yuan: 1 })
    expect(data.availability.fees.commission_fen).toEqual({ expected: 2, available: 2, complete: true })
  })

  it('does not present partial fee coverage as a total or fill absent slippage with zero', () => {
    const data = buildAtlasData(result(account({ events: [fill(), fill('ENTRY_FILLED', { fee_breakdown: { commission_fen: '0', stamp_tax_fen: '0' } })] })), 'oos')
    expect(data.fees.map(row => row.key)).toEqual(['commission_fen', 'stamp_tax_fen'])
    expect(data.availability.fees.other_fee_fen).toEqual({ expected: 2, available: 1, complete: false })
    expect(data.cashFees).toEqual({ fen: null, yuan: null })
    expect(data.slippage).toEqual({ fen: null, yuan: null })
  })

  it('retains exact oversized fee strings but refuses unsafe chart coordinates and PnL', () => {
    const data = buildAtlasData(result(account({ events: [fill('EXIT_FILLED', { realized_pnl_fen: '9007199254740993', fee_breakdown: { commission_fen: '9007199254740993', stamp_tax_fen: '0', other_fee_fen: '0', slippage_fen: '9007199254740993' } })] })), 'oos')
    expect(data.exits).toEqual([])
    expect(data.availability.exits.complete).toBe(false)
    expect(data.fees.find(row => row.key === 'commission_fen')).toBeUndefined()
    expect(data.cashFees).toEqual({ fen: '9007199254740993', yuan: null })
    expect(data.slippage).toEqual({ fen: '9007199254740993', yuan: null })
    expect(data.errors.some(row => row.code === 'AMOUNT_UNSAFE')).toBe(true)
  })

  it('excludes zero/negative/fractional fill quantity and contradictory filled:false, reporting incomplete coverage', () => {
    const events = [0, -1, .5].map(qty => fill('EXIT_FILLED', { qty }))
    events.push({ ...fill(), filled: false } as Event)
    const data = buildAtlasData(result(account({ events })), 'oos')
    expect(data.exits).toEqual([])
    expect(data.events.find(row => row.label === '卖出成交')?.count).toBe(0)
    expect(data.availability.exits).toEqual({ expected: 4, available: 0, complete: false })
    expect(data.availability.fees.commission_fen.complete).toBe(false)
    expect(data.cashFees.yuan).toBeNull()
  })

  it('rejects missing exit amounts, missing monthly values and nonfinite drawdown', () => {
    const data = buildAtlasData(result(account({ events: [fill('EXIT_FILLED', { realized_pnl_fen: undefined })],
      monthly: [{ month: '202501', net_return: '', net_pnl_fen: '0' }], equity_curve: [point('20250102', { drawdown: Infinity })],
    })), 'oos')
    expect(data.exits).toEqual([])
    expect(data.months[0].returnPct).toBeNull()
    expect(data.curve[0].drawdownPct).toBeNull()
    expect(data.metrics.worstDrawdownPct).toBeNull()
  })

  it('does not report zero fees as complete when the event stream itself is malformed', () => {
    const data = buildAtlasData(result(account({ events: [null] as unknown as Event[] })), 'oos')
    expect(data.fees).toEqual([])
    expect(data.cashFees.yuan).toBeNull()
    expect(data.slippage.yuan).toBeNull()
    expect(data.availability.exits.complete).toBe(false)
  })

  it('does not round a tiny nonzero account ratio into a claimed actual zero', () => {
    const data = buildAtlasData(result(account({ initial_equity_fen: '100000000000000000000',
      equity_curve: [point('20250102', { equity_fen: '1', cash_fen: '1', market_value_fen: '0' })],
    })), 'oos')
    expect(data.curve[0].nav).toBeNull()
    expect(data.errors.some(row => row.code === 'RATIO_UNSAFE')).toBe(true)
  })
})
