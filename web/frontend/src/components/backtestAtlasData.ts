import type { BacktestAccountDetails, BacktestResult } from '../api/client'

export type AtlasScope = 'is' | 'oos'
export interface AtlasCoverage { expected: number; available: number; complete: boolean }
export interface AtlasMoney { fen: string | null; yuan: number | null }
export interface AtlasError { code: string; section: string; message: string }
export interface AtlasData {
  scope: AtlasScope
  curve: { date: string; nav: number | null; drawdownPct: number | null; exposurePct: number | null; cashPct: number | null }[]
  months: { month: string; returnPct: number | null }[]
  /** Each point is one sell fill, including partial fills, not a completed round trip. */
  exits: { date: string; code: string; pnlYuan: number }[]
  events: { label: string; count: number }[]
  fees: { key: string; label: string; fen: string; yuan: number }[]
  cashFees: AtlasMoney
  /** Execution-price slippage estimate, already reflected in fills; never add to cashFees. */
  slippage: AtlasMoney
  metrics: {
    curveDays: number; exposureDays: number; averageExposurePct: number | null
    investedDays: number | null; winningMonths: number | null; availableMonths: number
    underwaterDays: number | null; worstDrawdownPct: number | null
  }
  availability: {
    account: boolean; curve: boolean; months: boolean; events: boolean
    exits: AtlasCoverage; fees: Record<string, AtlasCoverage>; slippage: AtlasCoverage
  }
  errors: AtlasError[]
}

type Row = Record<string, unknown>
type Event = BacktestAccountDetails['events'][number]
const FEE_FIELDS = [
  ['commission_fen', '佣金'], ['stamp_tax_fen', '印花税'], ['other_fee_fen', '其他费用'],
] as const
const EVENT_NAMES = [
  ['ENTRY_FILLED', '买入成交'], ['EXIT_FILLED', '卖出成交'],
  ['ENTRY_REJECTED', '买入未成交'], ['EXIT_RETRY', '卖出顺延'],
] as const
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER)
const RATIO_SCALE = 1_000_000_000_000n
const isRow = (value: unknown): value is Row => value !== null && typeof value === 'object' && !Array.isArray(value)
const coverage = (expected = 0, available = 0, present = false): AtlasCoverage => ({ expected, available, complete: present && expected === available })
const emptyMoney = (): AtlasMoney => ({ fen: null, yuan: null })

function normalizeDate(value: unknown): string | null {
  if (typeof value !== 'string' || !/^(?:\d{8}|\d{4}-\d{2}-\d{2})$/.test(value)) return null
  const date = value.replaceAll('-', '')
  const year = Number(date.slice(0, 4)), month = Number(date.slice(4, 6)), day = Number(date.slice(6, 8))
  const parsed = new Date(Date.UTC(year, month - 1, day))
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day ? date : null
}
function normalizeMonth(value: unknown): string | null {
  if (typeof value !== 'string' || !/^(?:\d{6}|\d{4}-\d{2})$/.test(value)) return null
  const month = value.replaceAll('-', '')
  return normalizeDate(`${month}01`) ? month : null
}
function integerFen(value: unknown): bigint | null {
  // Never pass a rounded JSON number, decimal, blank string or nonfinite amount to BigInt.
  if (typeof value === 'number') return Number.isSafeInteger(value) ? BigInt(value) : null
  if (typeof value !== 'string' || !/^[+-]?\d+$/.test(value)) return null
  try { return BigInt(value) } catch { return null }
}
function decimal(value: unknown): number | null {
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value))) return null
  const result = Number(value)
  return Number.isFinite(result) ? result : null
}
function ratio(numerator: bigint, denominator: bigint, percent = false): number | null {
  if (denominator <= 0n) return null
  // Divide in integer space before converting the bounded chart coordinate to Number.
  const scaled = numerator * RATIO_SCALE * (percent ? 100n : 1n) / denominator
  if (numerator !== 0n && scaled === 0n) return null
  return scaled >= -MAX_SAFE && scaled <= MAX_SAFE ? Number(scaled) / Number(RATIO_SCALE) : null
}
function yuan(fen: bigint): number | null {
  // A chart cannot retain individual fen beyond this range. Exact fen remains a string.
  return fen >= -MAX_SAFE && fen <= MAX_SAFE ? Number(fen) / 100 : null
}

/** Pure, scope-local adaptation. Consumers must use connectNulls:false for every curve. */
export function buildAtlasData(result: BacktestResult, scope: AtlasScope): AtlasData {
  const data: AtlasData = {
    scope, curve: [], months: [], exits: [], events: [], fees: [], cashFees: emptyMoney(), slippage: emptyMoney(),
    metrics: { curveDays: 0, exposureDays: 0, averageExposurePct: null, investedDays: null, winningMonths: null, availableMonths: 0, underwaterDays: null, worstDrawdownPct: null },
    availability: { account: false, curve: false, months: false, events: false, exits: coverage(), fees: Object.fromEntries(FEE_FIELDS.map(([key]) => [key, coverage()])), slippage: coverage() },
    errors: [],
  }
  const error = (code: string, section: string, message: string) => data.errors.push({ code, section, message })
  const details = result.account_details?.[scope]
  if (!isRow(details)) {
    error('ACCOUNT_MISSING', 'account', '所选区间没有保存账户明细，不从其他区间或样本交易均值补造。')
    return data
  }
  data.availability.account = true
  const rawWindow = result.request?.windows?.[scope]
  const windowStart = normalizeDate(rawWindow?.[0]), windowEnd = normalizeDate(rawWindow?.[1])
  const validWindow = windowStart !== null && windowEnd !== null && windowStart <= windowEnd
  const inWindow = (date: string) => !validWindow || date >= windowStart! && date <= windowEnd!
  const readFen = (value: unknown, section: string, label: string, nonnegative = false) => {
    const amount = integerFen(value)
    if (amount === null || nonnegative && amount < 0n) {
      error('AMOUNT_INVALID', section, `${label}缺失或不是有效的整数分金额。`)
      return null
    }
    return amount
  }
  const initial = readFen(details.initial_equity_fen, 'curve', '期初权益', true)
  if (initial === 0n) error('INITIAL_EQUITY_ZERO', 'curve', '期初权益为零，不能计算净值。')

  if (Array.isArray(details.equity_curve)) {
    data.availability.curve = true
    const dates = new Map<string, Row[]>()
    let unknownDate = false
    for (const row of details.equity_curve as unknown[]) {
      const date = isRow(row) ? normalizeDate(row.trade_date) : null
      if (!date) {
        unknownDate = true
        error('DATE_INVALID', 'curve', '账户曲线含无法定位的日期，整条路径暂不绘制。')
        continue
      }
      if (!inWindow(date)) { error('OUTSIDE_SCOPE', 'curve', `${date}不属于所选区间。`); continue }
      dates.set(date, [...(dates.get(date) || []), row as Row])
    }
    if (unknownDate) data.availability.curve = false
    else for (const [date, rows] of [...dates].sort(([a], [b]) => a.localeCompare(b))) {
      const point: AtlasData['curve'][number] = { date, nav: null, drawdownPct: null, exposurePct: null, cashPct: null }
      data.curve.push(point)
      if (rows.length !== 1) { error('DUPLICATE_DATE', 'curve', `${date}存在重复记录，该日全部曲线值留空。`); continue }
      const row = rows[0]
      const equity = readFen(row.equity_fen, 'curve', `${date}权益`, true)
      const market = readFen(row.market_value_fen, 'curve', `${date}持仓市值`, true)
      const cash = readFen(row.cash_fen, 'curve', `${date}现金`, true)
      if (equity !== null && initial !== null && initial > 0n) {
        point.nav = ratio(equity, initial)
        if (point.nav === null) error('RATIO_UNSAFE', 'curve', `${date}净值超出可靠绘图范围。`)
      }
      if (equity !== null && cash !== null && market !== null) {
        if (cash + market !== equity) error('EQUITY_MISMATCH', 'curve', `${date}现金与持仓市值之和不等于权益，仓位数据留空。`)
        else if (equity > 0n) {
          point.exposurePct = ratio(market, equity, true)
          point.cashPct = ratio(cash, equity, true)
          if (point.exposurePct === null || point.cashPct === null) error('RATIO_UNSAFE', 'curve', `${date}仓位比例超出可靠绘图精度。`)
        }
      }
      const drawdown = decimal(row.drawdown)
      if (drawdown !== null && drawdown >= 0 && drawdown <= 1) point.drawdownPct = drawdown * 100
      else error('DRAWDOWN_MISSING', 'curve', `${date}没有有效回撤记录，不重建未知路径。`)
    }
  } else error('CURVE_MISSING', 'curve', '账户日序列未记录。')

  if (Array.isArray(details.monthly)) {
    data.availability.months = true
    const months = new Map<string, Row[]>()
    for (const row of details.monthly as unknown[]) {
      const month = isRow(row) ? normalizeMonth(row.month) : null
      if (!month) { error('MONTH_INVALID', 'months', '月度记录的月份无效。'); continue }
      if (validWindow && (month < windowStart!.slice(0, 6) || month > windowEnd!.slice(0, 6))) {
        error('OUTSIDE_SCOPE', 'months', `${month}不属于所选区间。`); continue
      }
      months.set(month, [...(months.get(month) || []), row as Row])
    }
    for (const [month, rows] of [...months].sort(([a], [b]) => a.localeCompare(b))) {
      let returnPct: number | null = null
      if (rows.length !== 1) error('DUPLICATE_MONTH', 'months', `${month}存在重复月度记录，该月留空。`)
      else {
        const value = decimal(rows[0].net_return)
        if (value !== null && value >= -1 && Number.isFinite(value * 100)) returnPct = value * 100
        else error('MONTH_RETURN_MISSING', 'months', `${month}月收益缺失或无效。`)
      }
      data.months.push({ month, returnPct })
    }
  } else error('MONTHS_MISSING', 'months', '月度账户收益未记录。')

  if (Array.isArray(details.events)) {
    data.availability.events = true
    const counts = new Map<string, number>(EVENT_NAMES.map(([key]) => [key, 0]))
    const fills: { row: Event; valid: boolean; date: string }[] = []
    let expectedExits = 0
    let streamComplete = true
    for (const raw of details.events as unknown[]) {
      if (!isRow(raw) || typeof raw.event !== 'string') {
        streamComplete = false
        error('EVENT_INVALID', 'events', '事件记录无效，不能确认完整成交和费用覆盖。')
        continue
      }
      if (!counts.has(String(raw.event))) continue
      const date = normalizeDate(raw.trade_date)
      const inside = date !== null && inWindow(date)
      if (date !== null && !inside) { error('OUTSIDE_SCOPE', 'events', `${date}事件不属于所选区间。`); continue }
      const isFill = raw.event === 'ENTRY_FILLED' || raw.event === 'EXIT_FILLED'
      const valid = inside && typeof raw.ts_code === 'string' && raw.ts_code.trim().length > 0
        && (!isFill || typeof raw.qty === 'number' && Number.isSafeInteger(raw.qty) && raw.qty > 0 && raw.filled !== false)
      if (valid) counts.set(String(raw.event), counts.get(String(raw.event))! + 1)
      else error('EVENT_INVALID', 'events', '事件日期、股票或成交股数无效，不能作为实际成交绘图。')
      if (isFill) fills.push({ row: raw as unknown as Event, valid, date: date || '' })
      if (raw.event === 'EXIT_FILLED') {
        expectedExits++
        if (!valid) continue
        const pnl = readFen(raw.realized_pnl_fen, 'exits', `${date} ${raw.ts_code}卖出成交损益`)
        const pnlYuan = pnl === null ? null : yuan(pnl)
        if (pnl !== null && pnlYuan === null) error('AMOUNT_UNSAFE', 'exits', '卖出成交损益超出可保留分精度的绘图范围。')
        if (pnlYuan !== null) data.exits.push({ date: date!, code: String(raw.ts_code), pnlYuan })
      }
    }
    data.events = EVENT_NAMES.map(([key, label]) => ({ label, count: counts.get(key)! }))
    data.exits.sort((a, b) => a.date.localeCompare(b.date) || a.code.localeCompare(b.code))
    data.availability.exits = coverage(expectedExits, data.exits.length, streamComplete)
    let cashTotal = 0n, allCashComplete = true
    for (const [key, label] of [...FEE_FIELDS, ['slippage_fen', '滑点估计'] as const]) {
      let sum = 0n, available = 0
      for (const fill of fills) {
        if (!fill.valid) continue
        const amount = readFen(fill.row.fee_breakdown?.[key], 'fees', `${fill.date} ${label}`, true)
        if (amount !== null) { sum += amount; available++ }
      }
      const covered = coverage(fills.length, available, streamComplete)
      const coordinate = covered.complete ? yuan(sum) : null
      if (covered.complete && coordinate === null) error('AMOUNT_UNSAFE', 'fees', `${label}总额超出可保留分精度的绘图范围。`)
      const money: AtlasMoney = { fen: covered.complete ? sum.toString() : null, yuan: coordinate }
      if (key === 'slippage_fen') { data.availability.slippage = covered; data.slippage = money }
      else {
        data.availability.fees[key] = covered
        if (covered.complete) cashTotal += sum
        else allCashComplete = false
        if (money.fen !== null && money.yuan !== null) data.fees.push({ key, label, fen: money.fen, yuan: money.yuan })
      }
    }
    if (allCashComplete) {
      data.cashFees = { fen: cashTotal.toString(), yuan: yuan(cashTotal) }
      if (data.cashFees.yuan === null) error('AMOUNT_UNSAFE', 'fees', '现金费用总额超出可保留分精度的绘图范围。')
    }
  } else error('EVENTS_MISSING', 'events', '事件列表未记录，费用和成交次数不补零。')

  const exposures = data.curve.flatMap(row => row.exposurePct === null ? [] : [row.exposurePct])
  const drawdowns = data.curve.flatMap(row => row.drawdownPct === null ? [] : [row.drawdownPct])
  const returns = data.months.flatMap(row => row.returnPct === null ? [] : [row.returnPct])
  data.metrics = {
    curveDays: data.curve.filter(row => row.nav !== null).length,
    exposureDays: exposures.length,
    averageExposurePct: exposures.length ? exposures.reduce((sum, value) => sum + value, 0) / exposures.length : null,
    investedDays: exposures.length ? exposures.filter(value => value > 0).length : null,
    winningMonths: returns.length ? returns.filter(value => value > 0).length : null,
    availableMonths: returns.length,
    underwaterDays: drawdowns.length ? drawdowns.filter(value => value > 0).length : null,
    worstDrawdownPct: drawdowns.length ? Math.max(...drawdowns) : null,
  }
  return data
}
