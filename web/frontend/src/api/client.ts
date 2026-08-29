const BASE = '/api'

/** 默认请求超时：30s，超时后 abort。 */
const DEFAULT_TIMEOUT_MS = 30_000

/** 可传外部 AbortSignal 与自定义超时。 */
export type ReqOpts = { signal?: AbortSignal; timeoutMs?: number }

export class ApiError extends Error {
  code: string
  details: Record<string, unknown>
  retryable: boolean
  status: number

  constructor(args: {
    code: string
    message: string
    details?: Record<string, unknown>
    retryable?: boolean
    status: number
  }) {
    super(args.message)
    this.name = 'ApiError'
    this.code = args.code
    this.details = args.details || {}
    this.retryable = Boolean(args.retryable)
    this.status = args.status
  }
}

export async function request<T>(path: string, init?: RequestInit & ReqOpts): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init || {}
  const controller = new AbortController()
  const external = rest.signal
  // 外部已中止则立即 abort；外部后续中止时同步 abort 内部请求
  if (external?.aborted) controller.abort()
  const onExternalAbort = () => controller.abort()
  external?.addEventListener('abort', onExternalAbort)
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(BASE + path, {
      ...rest,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(rest.headers || {}) },
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detail = body?.detail || body?.message || `HTTP ${response.status}`
      if (typeof detail === 'string') {
        throw new ApiError({ code: 'HTTP_ERROR', message: detail, status: response.status })
      }
      throw new ApiError({
        code: typeof detail?.code === 'string' ? detail.code : 'HTTP_ERROR',
        message: typeof detail?.message === 'string' ? detail.message : `HTTP ${response.status}`,
        details: typeof detail?.details === 'object' && detail.details ? detail.details : {},
        retryable: Boolean(detail?.retryable),
        status: response.status,
      })
    }
    return body as T
  } finally {
    clearTimeout(timer)
    external?.removeEventListener('abort', onExternalAbort)
  }
}

export const newIdempotencyKey = () =>
  globalThis.crypto?.randomUUID?.() || `paper-${Date.now()}-${Math.random().toString(16).slice(2)}`

export function paperWrite<T>(path: string, body: unknown, opts?: ReqOpts): Promise<T> {
  return request<T>(path, {
    ...opts,
    method: 'POST',
    headers: { 'Idempotency-Key': newIdempotencyKey() },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
}

export interface KlinePoint {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  vol: number
  amount: number | null
}

export interface FinaRow {
  ann_date: string
  end_date: string
  roe: number | null
  roe_waa: number | null
  roa: number | null
  grossprofit_margin: number | null
  netprofit_margin: number | null
  or_yoy: number | null
  netprofit_yoy: number | null
  debt_to_assets: number | null
  current_ratio: number | null
  quick_ratio: number | null
  ocf_to_or: number | null
  eps: number | null
  bps: number | null
}

export interface TradeCard {
  entry_ref: number | null
  stop_loss: number | null
  target_1: number | null
  target_2: number | null
  position_pct: number
  max_hold_days: number
  tradeable: boolean
  pool: string
  stop_rule?: string
  target_rule?: string
}

export interface OverviewItem {
  ts_code: string
  code: string
  name: string
  price: number | null
  industry: string
  mv_yi: number | null
  pe: number | null
  pb: number | null
  turnover: number | null
  score: number
  box_days: number | null
  box_amp: number | null
  vol_ratio: number | null
  fund_net_wan: number | null
  fund_ratio: number | null
  breakout_date: string
  reasons: string
  pool?: string
  tier?: string
  tradeable?: boolean
  trade?: TradeCard
  fina?: FinaRow[]
  kline?: KlinePoint[]
  box_high?: number | null
  box_low?: number | null
  ma5?: number | null
  ma20?: number | null
}

export interface Freshness {
  as_of: string
  today: string
  stale_days: number
  is_stale: boolean
  label: string
  unit?: string
  expected_as_of?: string
  stale_label?: string
}

export interface Regime {
  regime: string
  label: string
  allow_new_entries?: boolean
  max_trade_slots?: number
  notes?: string[]
}

export interface OverviewResp {
  as_of: string
  count: number
  pool?: string
  items: OverviewItem[]
  freshness?: Freshness
  regime?: Regime
  pool_totals?: { A: number; B: number }
  empty_reason?: string | null
}

export interface HealthResp {
  status: string
  time: string
  as_of?: string
  freshness?: Freshness
  regime?: Regime
  guided_ui_enabled?: boolean
}

export type TodayAction =
  | 'SYNC_DATA'
  | 'WAIT_SCAN'
  | 'RUN_SCAN'
  | 'CREATE_ACCOUNT'
  | 'RESOLVE_RECONCILIATION'
  | 'REVIEW_DRAFT'
  | 'RUN_SETTLEMENT'
  | 'DAILY_COMPLETE'

export interface TodayGuide {
  next_action: TodayAction
  title: string
  reason: string
  primary_label: string
  href: string | null
  latest_market_date?: string | null
  expected_market_date?: string | null
  trade_date?: string | null
  task_id?: string | null
  task_status?: string | null
  order_id?: string | null
  cycle_id?: string | null
  scan_run_id?: string | null
  blocker_codes?: string[]
}

export interface SetupStatus {
  has_token: boolean
  has_frontend_dist: boolean
  latest_daily: string | null
  latest_moneyflow: string | null
  has_market_data: boolean
  scan_result_rows: number
  ui_mode: string
  open_url: string
  tips: string[]
}

export interface StockDetail {
  ts_code: string
  name: string
  industry: string
  area: string
  list_date: string
  kline: KlinePoint[]
  signal: {
    box_high: number | null
    box_low: number | null
    box_days: number | null
    box_amp: number | null
    breakout_date: string | null
    breakout_vol_ratio: number | null
    breakout_pct_chg: number | null
    vol_shrink_ratio: number | null
    ma5: number | null
    ma10: number | null
    ma20: number | null
    reasons: string[]
  }
  fundamentals: {
    pe: number | null
    pb: number | null
    total_mv_wan: number | null
    circ_mv_wan: number | null
    turnover_rate: number | null
    volume_ratio: number | null
    close: number | null
  }
  fund_flow: {
    net_wan: number
    score: number
    ratio_pct: number
    days: number
  }
  fina?: FinaRow[]
  as_of: string
  trade?: TradeCard
}

export interface SectorFlowResp {
  dates: string[]
  days: number
  industries: Record<string, number[]>
  top_in: { industry: string; net_wan: number }[]
  top_out: { industry: string; net_wan: number }[]
}

export interface StockFlowResp {
  ts_code: string
  name: string
  industry: string
  days: number
  stock_flow: {
    trade_date: string
    net_wan: number
    buy_main_wan: number
    sell_main_wan: number
    buy_elg_wan: number
    buy_lg_wan: number
  }[]
  sector_flow: { dates: string[]; net_wan: number[] }
  as_of: string
}

export interface ScanStatus {
  id: string
  status: 'pending' | 'running' | 'done' | 'error' | 'cancelled' | 'cancelling' | 'idle'
  stage: string
  progress: number
  cancel_requested: boolean
  result?: Record<string, unknown> | null
  error?: string | null
}

export const api = {
  today: (opts?: ReqOpts) => request<TodayGuide>('/today', opts),
  health: (opts?: ReqOpts) => request<HealthResp>('/health', opts),
  setupStatus: (opts?: ReqOpts) => request<SetupStatus>('/setup-status', opts),
  overview: (pool = 'A', opts?: ReqOpts) => request<OverviewResp>(`/overview?pool=${pool}`, opts),
  stock: (tsCode: string, opts?: ReqOpts) => request<StockDetail>(`/stock/${encodeURIComponent(tsCode)}`, opts),
  stockFlow: (tsCode: string, days = 20, opts?: ReqOpts) =>
    request<StockFlowResp>(`/stock/${encodeURIComponent(tsCode)}/flow?days=${days}`, opts),
  sectorFlow: (days = 10, opts?: ReqOpts) => request<SectorFlowResp>(`/sector-flow?days=${days}`, opts),
  scan: (top = 15, days = 160, force = false, opts?: ReqOpts) =>
    request<{ status: string; task_id: string; top: number; days: number }>('/scan', {
      ...opts,
      method: 'POST',
      body: JSON.stringify({ top, days, force }),
    }),
  scanStatus: (taskId?: string, opts?: ReqOpts) =>
    request<ScanStatus>(taskId ? `/scan/status?task_id=${taskId}` : '/scan/status', opts),
  cancelScan: (taskId: string, opts?: ReqOpts) =>
    request<{ status: string; stage: string }>(`/scan/${taskId}/cancel`, { ...opts, method: 'POST' }),
  labOptimize: (body: LabOptimizeBody, opts?: ReqOpts) =>
    request<LabOptimizeResp>('/lab/optimize', { ...opts, method: 'POST', body: JSON.stringify(body) }),
  labStatus: (taskId?: string, opts?: ReqOpts) =>
    request<LabStatusResp>(taskId ? `/lab/status?task_id=${taskId}` : '/lab/status', opts),
  labCancel: (taskId: string, opts?: ReqOpts) =>
    request<{ status: string; task_id?: string; msg?: string }>(`/lab/${taskId}/cancel`, { ...opts, method: 'POST' }),
  labLeaderboard: (kind = 'IS', strategy = 'A', limit = 20, opts?: ReqOpts) =>
    request<LabBoardResp>(`/lab/leaderboard?kind=${kind}&strategy=${strategy}&limit=${limit}`, opts),
  labCompare: (ids = '', opts?: ReqOpts) =>
    request<LabCompareResp>(`/lab/compare${ids ? `?ids=${ids}` : ''}`, opts),
  labArena: (opts?: ReqOpts) => request<LabArenaResp>('/lab/arena', opts),
  labResearchStatus: (probeToken = false, opts?: ReqOpts) =>
    request<LabResearchStatus>(`/lab/research-status?probe_token=${probeToken ? 'true' : 'false'}`, opts),
  labCatalog: (opts?: ReqOpts) => request<LabCatalog>('/lab/catalog', opts),
  labLatestReport: (opts?: ReqOpts) => request<LabReportEnvelope>('/lab/reports/latest', opts),
  labReports: (limit = 20, opts?: ReqOpts) =>
    request<{ items: LabReportHistoryItem[] }>(`/lab/reports?limit=${limit}`, opts),
  labReport: (runId: string, opts?: ReqOpts) =>
    request<LabReportEnvelope>(`/lab/reports/${encodeURIComponent(runId)}`, opts),

  // ── 纸面交易 ──
  paperAccount: (opts?: ReqOpts) => request<PaperAccount>('/paper/account', opts),
  paperCreateAccount: (initialCashFen: number, opts?: ReqOpts) =>
    paperWrite<PaperAccount>('/paper/account', { initial_cash_fen: String(initialCashFen) }, opts),
  paperDashboard: (opts?: ReqOpts) => request<PaperDashboard>('/paper/dashboard', opts),
  paperTradingCalendar: (start: string, end: string, opts?: ReqOpts) =>
    request<PaperTradingCalendar>(`/paper/trading-calendar?start=${start}&end=${end}`, opts),
  paperReviewOrder: (body: PaperOrderReviewRequest, opts?: ReqOpts) =>
    request<PaperOrderReview>('/paper/orders/review', {
      ...opts, method: 'POST', body: JSON.stringify(body),
    }),
  paperPositions: (opts?: ReqOpts) => request<{ positions: PaperPosition[] }>('/paper/positions', opts),
  paperOrders: (state?: string, opts?: ReqOpts) =>
    request<{ orders: PaperOrder[] }>(`/paper/orders${state ? `?state=${state}` : ''}`, opts),
  paperCreateDraft: (body: Record<string, unknown>, opts?: ReqOpts) =>
    paperWrite<PaperOrder>('/paper/orders/drafts', body, opts),
  paperConfirmOrder: (orderId: string, opts?: ReqOpts) =>
    paperWrite<PaperOrder>(`/paper/orders/${orderId}/confirm`, undefined, opts),
  paperCancelOrder: (orderId: string, opts?: ReqOpts) =>
    paperWrite<PaperOrder>(`/paper/orders/${orderId}/cancel`, undefined, opts),
  paperFills: (limit = 50, opts?: ReqOpts) => request<{ fills: PaperFill[] }>(`/paper/fills?limit=${limit}`, opts),
  paperRunCycle: (tradeDate: string, opts?: ReqOpts) =>
    paperWrite<PaperCycleResult>('/paper/cycles/run', { trade_date: tradeDate }, opts),
  paperCycleStatus: (tradeDate: string, opts?: ReqOpts) =>
    request<{ trade_date: string; phase: string | null; blocked_reason?: string }>(`/paper/cycles/${tradeDate}`, opts),
  paperImportPreview: (path: string, opts?: ReqOpts) =>
    paperWrite<PaperImportPreview>('/paper/import/preview', { path }, opts),
  paperImportCommit: (path: string, opts?: ReqOpts) =>
    paperWrite<{ imported: number; skipped_existing: boolean; positions: unknown[] }>('/paper/import/commit', { path }, opts),
  paperReconciliation: (tradeDate?: string, opts?: ReqOpts) =>
    request<{ items: Record<string, unknown>[] }>(tradeDate ? `/paper/reconciliation?trade_date=${tradeDate}` : '/paper/reconciliation', opts),
  paperRunReconciliation: (tradeDate: string, opts?: ReqOpts) =>
    paperWrite<{ result: string; diffs: unknown[] }>('/paper/reconciliation/run', { trade_date: tradeDate }, opts),
  paperCorporateActions: (status?: string, opts?: ReqOpts) =>
    request<{ items: PaperCorporateAction[] }>(`/paper/corporate-actions${status ? `?status=${status}` : ''}`, opts),
  paperApplyCorporateAction: (actionId: number, opts?: ReqOpts) =>
    paperWrite<{ action_id: number; status: string }>(`/paper/corporate-actions/${actionId}/apply`, undefined, opts),
  paperGates: (opts?: ReqOpts) => request<Record<string, unknown>>('/paper/gates/status', opts),
  releaseReadiness: (opts?: ReqOpts) => request<ReleaseReadiness>('/release/readiness', opts),

  // 最新交易日资金热力图；top 表示流入、流出每个方向各取多少项。
  moneyHeatmap: (top = 10, opts?: ReqOpts) => request<MoneyHeatmapResp>(`/money-heatmap?top=${top}`, opts),

  // ── 数据同步（手动更新行情）──
  syncStart: (opts?: ReqOpts) => request<{ status: string; message: string }>('/sync', { ...opts, method: 'POST' }),
  syncStatus: (opts?: ReqOpts) => request<SyncStatus>('/sync/status', opts),

  // ── 回测工作台 ──
  backtestRun: (body: BacktestRunBody, opts?: ReqOpts) =>
    request<{ task_id: string }>('/backtest/run', { ...opts, method: 'POST', body: JSON.stringify(body) }),
  backtestStatus: (taskId: string, opts?: ReqOpts) => request<BacktestTaskStatus>(`/backtest/status/${taskId}`, opts),
  backtestCancel: (taskId: string, opts?: ReqOpts) =>
    request<{ ok: boolean }>(`/backtest/${taskId}/cancel`, { ...opts, method: 'POST' }),

  // ── 通用 K 线（回测工作台交易标注图）──
  kline: (tsCode: string, start?: string, end?: string, opts?: ReqOpts) => {
    const qs = new URLSearchParams()
    if (start) qs.set('start', start)
    if (end) qs.set('end', end)
    return request<KlineRangeResp>(`/kline/${encodeURIComponent(tsCode)}${qs.toString() ? `?${qs}` : ''}`, opts)
  },
}

export interface KlineRangeResp {
  ts_code: string
  kline: KlinePoint[]
}

export interface SyncStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  message: string
  started_at: string | null
  finished_at: string | null
  latest_daily: string | null
  latest_moneyflow: string | null
  failed_dates: string[]
}

export interface BacktestRunBody {
  strategy: 'A' | 'B'
  vol_ratio_min?: number
  stop_pct?: number
  exit_window?: number
  strong_reset?: number
  signal?: Record<string, number | boolean | null>
  costs?: {
    commission_rate?: number
    commission_min?: number
    stamp_tax_sell?: number
    other_fee_rate?: number
    slippage?: number
  } | null
  windows?: { mode: 'auto' | 'manual'; is_start?: string; is_end?: string; oos_start?: string; oos_end?: string }
  max_codes?: number
  step?: number
  include_wf?: boolean
  include_baselines?: boolean
}

export interface BacktestTrade {
  ts_code: string
  signal_date: string
  entry_date: string
  exit_date: string
  box_high: number | null
  box_low: number | null
  breakout_date: string
  entry_price: number | null
  exit_price: number | null
  exit: string
  ret: number
  net_return: number | null
  filled: boolean
  days: number | null
  max_dd: number | null
  commission: number | null
  stamp_tax: number | null
  other_fee: number | null
  slippage_cost: number | null
  reason: string
}

export interface BacktestMetrics {
  n_trades?: number
  win_rate?: number | null
  avg_ret?: number | null
  profit_factor?: number | null
  net_n_trades?: number
  net_unfilled?: number
  net_pnl?: number
  net_avg_return?: number | null
  net_win_rate?: number | null
  net_profit_factor?: number | null
  net_max_drawdown?: number | null
  commission?: number
  stamp_tax?: number
  other_fee?: number
  slippage_cost?: number
  exits?: Record<string, number>
}

export interface BacktestEquityPoint {
  date: string
  ts_code: string
  eq: number
  drawdown: number
}

export interface BacktestSingleResult {
  universe_n: number
  sample_days: string[]
  window: [string, string]
  params: Record<string, unknown>
  trades: BacktestTrade[]
  metrics: BacktestMetrics
  equity: BacktestEquityPoint[]
  error?: string
}

export interface BacktestTaskResult {
  task_id: string
  params: Record<string, unknown>
  windows: { mode: string; is: [string, string]; oos: [string, string] }
  is: BacktestSingleResult
  oos: BacktestSingleResult
  hold_ratio: { pf: number | null }
  wf: Record<string, unknown> | null
  baselines: Record<string, unknown> | null
  disclaimer: string
}

export interface BacktestTaskStatus {
  status: 'running' | 'done' | 'error' | 'cancelled'
  stage: string
  progress: number
  started_at: string
  cancel_requested: boolean
  result: BacktestTaskResult | null
  error: string | null
}

export interface MoneyHeatmapResp {
  trade_date: string
  total_wan: number
  items: { name: string; value: number; net_wan: number }[]
}

export interface LabOptimizeBody {
  strategy: string
  is_start?: string
  is_end?: string
  oos_start?: string
  oos_end?: string
  max_codes?: number
  step?: number
  mode?: 'grid' | 'single'
  grid?: Record<string, number[]>
  vol_ratio_min?: number
  strong_reset?: number
  exit_window?: number
  stop_pct?: number
  force?: boolean
}

export interface LabParamDoc {
  key: string
  name: string
  unit: string
  meaning: string
  affects: string
  default: number
  options: number[]
  range_hint: string
}

export interface LabStrategyDoc {
  id: string
  name: string
  tagline: string
  entry_title: string
  entry_steps: string[]
  exit_title: string
  exit_steps: string[]
  fixed_note: string
}

export interface LabCatalog {
  strategies: Record<string, LabStrategyDoc>
  params: LabParamDoc[]
  grid_default: Record<string, number[]>
  grid_combo_count: number
  defaults: Record<string, number>
  pipeline: { id: string; name: string; desc: string }[]
  disclaimer: string
}

export interface LabWindows {
  is_start?: string
  is_end?: string
  oos_start?: string
  oos_end?: string
  mode?: string
  label?: string
  can_claim_edge?: boolean
  notes?: string[]
  n_dates?: number
  automatic_window?: boolean
  wf_windows?: {
    train_start: string
    train_end: string
    test_start: string
    test_end: string
  }[]
}

export interface LabOptimizeResp {
  status: string
  task_id: string
  strategy: string
  research_mode?: string
  can_claim_edge?: boolean
  windows?: LabWindows
}

export interface LabResearchPlan {
  mode: string
  label: string
  is_start: string
  is_end: string
  oos_start: string
  oos_end: string
  n_dates: number
  earliest?: string | null
  latest?: string | null
  is_n_dates: number
  oos_n_dates: number
  can_claim_edge: boolean
  data_ready_for_edge_validation?: boolean
  notes?: string[]
}

export interface LabMetricRow {
  param_id?: string
  strategy?: string
  vol_ratio_min?: number
  strong_reset?: number
  exit_window?: number
  stop_pct?: number
  n_trades?: number
  win_rate?: number | null
  profit_factor?: number | null
  max_drawdown?: number | null
  net_n_trades?: number
  net_unfilled?: number
  net_pnl?: number
  net_avg_return?: number | null
  net_win_rate?: number | null
  net_profit_factor?: number | null
  net_max_drawdown?: number | null
  commission?: number
  stamp_tax?: number
  other_fee?: number
  slippage_cost?: number
  oos_n_trades?: number
  oos_win_rate?: number | null
  oos_profit_factor?: number | null
  oos_max_drawdown?: number | null
  oos_net_n_trades?: number
  oos_net_pnl?: number
  oos_net_avg_return?: number | null
  oos_net_win_rate?: number | null
  oos_net_profit_factor?: number | null
  oos_net_max_drawdown?: number | null
  is_n_trades?: number
  is_win_rate?: number | null
  is_profit_factor?: number | null
  is_max_drawdown?: number | null
  is_net_n_trades?: number
  is_net_win_rate?: number | null
  is_net_profit_factor?: number | null
  is_net_max_drawdown?: number | null
  status?: string
  wf_pass?: boolean | number | null
}

export interface LabPromotionChecks {
  promotable?: boolean
  block_reason?: string
  research_mode_full?: boolean
  oos_net_pf_ok?: boolean
  oos_dd_ok?: boolean
  oos_wr_ok?: boolean
  beats_baseline?: boolean
}

export interface LabGateCheck {
  id: string
  label: string
  passed: boolean
  actual: unknown
  threshold: string
}

export interface LabWfWindow {
  window: string
  train_pf?: number | null
  test_pf?: number | null
  test_dd?: number | null
  test_wr?: number | null
  test_n?: number | null
}

export interface LabBaselineResult {
  baseline?: string
  seed?: number
  n_trades?: number
  requested_trades?: number
  net_avg_return?: number | null
  net_win_rate?: number | null
  net_profit_factor?: number | null
  net_max_drawdown?: number | null
}

export interface LabTrustedReport {
  research_run_id: string
  verdict: 'PASS' | 'FAIL' | 'INSUFFICIENT_EVIDENCE'
  candidate_eligible: boolean
  summary: string
  block_reasons: string[]
  versions: { dataset?: string; code?: string; cost?: string }
  sample: { universe_size?: number; step?: number; windows?: LabWindows }
  cost_assumptions: Record<string, unknown>
  primary_is?: LabMetricRow | null
  primary_oos?: LabMetricRow | null
  wf_windows: LabWfWindow[]
  baselines: { random?: LabBaselineResult; ma20_60?: LabBaselineResult }
  checks: LabGateCheck[]
  sensitivity: LabMetricRow[]
  markdown?: string
}

export interface LabReportHistoryItem {
  research_run_id: string
  status: string
  strategy?: string
  research_mode?: string
  verdict?: string
  candidate_eligible?: boolean
  created_at?: string
  finished_at?: string
  report_sha256?: string
}

export interface LabReportEnvelope extends LabReportHistoryItem {
  report: LabTrustedReport
}

export interface LabResearchStatus {
  as_of_check: string
  plan: LabResearchPlan
  token?: { ok?: boolean | null; error?: string | null }
  need_backfill: boolean
  next_steps: string[]
  disclaimer: string
}

export interface LabStatusResp {
  task_id?: string | null
  status: string
  research_run_id?: string
  phase?: string | null
  progress?: number
  message?: string
  error?: string | null
  result?: {
    is_top: LabMetricRow[]
    is_all?: LabMetricRow[]
    oos: LabMetricRow[]
    msg?: string
    run_mode?: string
    research_mode?: string
    can_claim_edge?: boolean
    gross?: { note?: string }
    net?: LabMetricRow[]
    promotion_checks?: LabPromotionChecks
    params_used?: Record<string, number[]> | Record<string, number> | null
    windows?: LabWindows
    baselines?: { random?: LabBaselineResult; ma20_60?: LabBaselineResult }
    trusted_report?: LabTrustedReport
  } | null
  strategy?: string
  windows?: LabWindows
  verdict?: string | null
  candidate_eligible?: boolean
}

export interface LabBoardResp {
  rows: LabMetricRow[]
  source: string
}

export interface LabCompareResp {
  rows?: LabMetricRow[]
  best_by_strategy?: Record<string, LabMetricRow | null>
}

export interface LabArenaResp {
  rows: LabMetricRow[]
  weights: Record<string, number>
}

// ── 纸面交易（paper trading）──

export interface PaperAccount {
  account_id: number
  initial_cash_fen: number
  status: string
  config_version: number
  created_at: string
  updated_at: string
  cash_fen: number
}

export interface PaperPosition {
  ts_code: string
  total_qty: number
  sellable_qty: number
  avg_cost_micro: number
}

export interface PaperOrder {
  order_id: string
  idempotency_key?: string
  source?: string
  ts_code: string
  side: string
  qty: number
  state: string
  reserve_fen: number
  reserved_qty?: number
  signal_trade_date?: string | null
  confirmed_at?: string | null
  eligible_trade_date?: string | null
  reject_reason: string | null
  created_at: string
}

export interface PaperFill {
  fill_id: string
  order_id: string
  ref_open_price_micro: number
  fill_price_micro: number
  qty: number
  commission_fen: number
  tax_fen: number
  fill_model_version: string
  quote_revision: string
  filled_at: string
}

export interface PaperDashboard {
  account: PaperAccount | null
  equity: {
    cash_fen: number
    market_value_fen: number
    total_equity_fen: number
    positions: number
  } | null
  equity_curve?: {
    trade_date: string
    cash_fen: number
    market_value_fen: number
    total_asset_fen: number
    realized_pnl_fen: number | null
    unrealized_pnl_fen: number | null
    drawdown_fen: number | null
  }[]
  risk?: {
    gross_exposure_limit_pct: string
    cash_buffer_pct: string
    daily_buy_limit_pct: string
    single_instrument_limit_pct: string
    reserved_cash_fen: number
    reserved_sell_qty: number
  }
  unresolved_reconciliation_count?: number
  paper_notice: string
  guide?: PaperGuide
}

export type PaperNextAction =
  | 'CREATE_ACCOUNT'
  | 'REVIEW_DRAFT'
  | 'RUN_SETTLEMENT'
  | 'RESOLVE_RECONCILIATION'
  | 'START_SIMULATION'
  | 'SYNC_DATA'

export interface PaperGuide {
  next_action: PaperNextAction
  blocker_codes: string[]
  pending_order: Pick<PaperOrder, 'order_id' | 'source' | 'ts_code' | 'side' | 'qty' | 'state' | 'eligible_trade_date'> | null
  earliest_simulation_date: string | null
  latest_market_date: string | null
  unresolved_reconciliation_count: number
}

export interface ReleaseReadiness {
  status: 'PASS' | 'FAIL'
  ready: boolean
  blockers: { code: string; message: string }[]
  identity: {
    git_sha: string
    worktree_clean: boolean
    worktree_fingerprint: string
    code_version: string
    config_hash: string
    db_fingerprint: string
  }
  gate_report_sha256: string | null
  gate_generated_at: string | null
  checked_at: string
}

export interface PaperTradingCalendar {
  open_dates: string[]
  earliest_simulation_date: string | null
  latest_market_date: string | null
}

export interface PaperOrderReviewRequest {
  scope: 'ACCOUNT' | 'TUTORIAL'
  side: 'BUY' | 'SELL'
  mode: 'MANUAL_HISTORY'
  ts_code: string
  execution_trade_date: string
  qty: number
}

export interface PaperOrderReview {
  scope: 'ACCOUNT' | 'TUTORIAL'
  persisted: false
  can_confirm: boolean
  instrument: { ts_code: string; inst_type: string; lot_size: number }
  side: 'BUY' | 'SELL'
  mode: 'MANUAL_HISTORY'
  decision_date: string
  execution_trade_date: string
  quote: {
    open: string; high: string; low: string; close: string
    volume: string; revision: string
  }
  estimate: {
    requested_qty: number
    estimated_fill_qty: number
    max_fill_qty: number
    fill_price: string
    notional_yuan: string
    commission_yuan: string
    tax_yuan: string
    other_fee_yuan: string
    reserve_yuan: string
    cash_change_yuan: string
    remaining_cash_yuan: string
  }
  checks: { code: string; label: string; passed: boolean; message: string }[]
  assumptions: {
    slippage_bps: number
    commission_bps: number
    sell_tax_bps: number
    participation_limit_pct: string
  }
}

export interface PaperCorporateAction {
  action_id: number
  ts_code: string
  ex_date: string
  kind: string
  amount_fen: number | null
  ratio: number | null
  note: string | null
  status: string
  applied_at: string | null
  adjustment_ref: string | null
}

export interface PaperImportPreview {
  source_file: string
  source_hash: string
  total: number
  valid_count: number
  invalid_count: number
  items: {
    ts_code: string
    name: string
    cost: number | null
    shares: number | null
    stop_loss: number | null
    opened_at: string
    last_close: number | null
    last_date: string | null
    errors: string[]
    valid: boolean
  }[]
  has_invalid: boolean
}

export interface PaperCycleResult {
  filled_count: number
  zero_fill_count: number
  mark: {
    cash_fen: number
    market_value_fen: number
    total_asset_fen: number
    unrealized_pnl_fen: number
    trade_date: string
    holdings: { ts_code: string; qty: number; close: number; market_value_fen: number }[]
  }
  reconciliation: { result: string; diffs: unknown[] }
  snapshot_ok: boolean
}
