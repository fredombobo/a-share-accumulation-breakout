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
  classification: ClassificationKey
  classification_title: string
  group_label: string
  dates: string[]
  days: number
  groups: Record<string, number[]>
  industries: Record<string, number[]>
  top_in: { group: string; industry: string; net_wan: number }[]
  top_out: { group: string; industry: string; net_wan: number }[]
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
  created_at?: string | null
  started_at?: string | null
  updated_at?: string | null
  heartbeat_at?: string | null
  finished_at?: string | null
  strategy_profile?: StrategyProfileSnapshot | null
  config_hash?: string | null
}

export const api = {
  today: (opts?: ReqOpts) => request<TodayGuide>('/today', opts),
  health: (opts?: ReqOpts) => request<HealthResp>('/health', opts),
  setupStatus: (opts?: ReqOpts) => request<SetupStatus>('/setup-status', opts),
  overview: (pool = 'A', opts?: ReqOpts) => request<OverviewResp>(`/overview?pool=${pool}`, opts),
  stock: (tsCode: string, opts?: ReqOpts) => request<StockDetail>(`/stock/${encodeURIComponent(tsCode)}`, opts),
  stockFlow: (tsCode: string, days = 20, opts?: ReqOpts) =>
    request<StockFlowResp>(`/stock/${encodeURIComponent(tsCode)}/flow?days=${days}`, opts),
  classifications: (opts?: ReqOpts) => request<ClassificationCatalogResp>('/classifications', opts),
  sectorFlow: (days = 10, classification: ClassificationKey = 'industry', opts?: ReqOpts) =>
    request<SectorFlowResp>(`/sector-flow?days=${days}&classification=${encodeURIComponent(classification)}`, opts),
  scan: (top = 15, days = 160, force = false, opts?: ReqOpts) =>
    request<{ status: string; task_id: string; top: number; days: number; requested_days: number; config_hash: string; strategy_profile: StrategyProfileSnapshot }>('/scan', {
      ...opts,
      method: 'POST',
      body: JSON.stringify({ top, days, force }),
    }),
  scanStatus: (taskId?: string, opts?: ReqOpts) =>
    request<ScanStatus>(taskId ? `/scan/status?task_id=${taskId}` : '/scan/status', opts),
  cancelScan: (taskId: string, opts?: ReqOpts) =>
    request<{ status: string; stage: string }>(`/scan/${taskId}/cancel`, { ...opts, method: 'POST' }),

  // ── 专业多参数回测 ──
  backtestCatalog: (opts?: ReqOpts) => request<BacktestCatalog>('/backtest/catalog', opts),
  backtestUniverse: (
    classification: ClassificationKey = 'industry',
    group?: string,
    opts?: ReqOpts,
  ) => {
    const query = new URLSearchParams({ classification })
    if (group) query.set('group', group)
    return request<BacktestUniverseCatalog>(`/backtest/universe?${query.toString()}`, opts)
  },
  backtestPreview: (body: BacktestRequest, opts?: ReqOpts) =>
    request<BacktestPreview>('/backtest/preview', { ...opts, method: 'POST', body: JSON.stringify(body) }),
  backtestRun: (body: BacktestRequest, opts?: ReqOpts) =>
    request<{ task_id: string; status: string; cached: boolean }>('/backtest/run', { ...opts, method: 'POST', body: JSON.stringify(body) }),
  backtestLatest: (opts?: ReqOpts) => request<{ task: BacktestTask | null; profile_activation: ProfileActivation }>('/backtest/latest', opts),
  backtestStatus: (taskId: string, opts?: ReqOpts) => request<BacktestTask>(`/backtest/status/${encodeURIComponent(taskId)}`, opts),
  backtestCancel: (taskId: string, opts?: ReqOpts) => request<BacktestTask>(`/backtest/${encodeURIComponent(taskId)}/cancel`, { ...opts, method: 'POST' }),
  backtestProfile: (opts?: ReqOpts) => request<StrategyProfileState>('/backtest/profile', opts),
  activateBacktestProfile: (taskId: string, opts?: ReqOpts) =>
    request<StrategyProfileState & { activation: ProfileActivation; idempotent: boolean }>('/backtest/profile/activate', {
      ...opts,
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, acknowledge_exploratory: true }),
    }),
  resetBacktestProfile: (opts?: ReqOpts) =>
    request<StrategyProfileState & { retired_count: number }>('/backtest/profile/reset', {
      ...opts,
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    }),
  saveManualResearchProfile: (parameters: ManualStrategyParameters, opts?: ReqOpts) =>
    request<StrategyProfileState & { idempotent: boolean }>('/backtest/profile/manual', {
      ...opts,
      method: 'POST',
      body: JSON.stringify({ parameters, acknowledge_research_only: true }),
    }),

  // ── 个股 AI 证据评测 ──
  aiReview: (tsCode: string, opts?: ReqOpts) => request<AIReview>(`/ai-review/${encodeURIComponent(tsCode)}`, opts),
  aiReviewGenerate: (tsCode: string, provider = 'deepseek', opts?: ReqOpts) =>
    request<{ review: AIReview; generated: ExternalAIInsight }>(`/ai-review/${encodeURIComponent(tsCode)}/generate`, {
      ...opts, method: 'POST', body: JSON.stringify({ provider }),
    }),

  // 最新交易日资金热力图；top 表示流入、流出每个方向各取多少项。
  moneyHeatmap: (top = 10, classification: ClassificationKey = 'industry', opts?: ReqOpts) =>
    request<MoneyHeatmapResp>(`/money-heatmap?top=${top}&classification=${encodeURIComponent(classification)}`, opts),

  // ── 数据同步（手动更新行情）──
  syncStart: (opts?: ReqOpts) => request<{ status: string; message: string }>('/sync', { ...opts, method: 'POST' }),
  syncStatus: (opts?: ReqOpts) => request<SyncStatus>('/sync/status', opts),

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

export interface MoneyHeatmapResp {
  classification: ClassificationKey
  classification_title: string
  group_label: string
  trade_date: string
  total_wan: number
  items: { name: string; value: number; net_wan: number }[]
}

export type ClassificationKey = 'industry' | 'market' | 'area'

export interface ClassificationDefinition {
  key: ClassificationKey
  title: string
  group_label: string
  description: string
  pit_status: 'CURRENT_SNAPSHOT_ONLY'
  available: boolean
  group_count: number
  coverage_pct: number
  examples: string[]
}

export interface ClassificationCatalogResp {
  default: ClassificationKey
  items: ClassificationDefinition[]
  limitations: string
}

// ── 专业回测 ──
export type ParameterMode = 'fixed' | 'range' | 'values'
export type ParameterSpec =
  | { mode: 'fixed'; value: number | boolean }
  | { mode: 'range'; start: number; stop: number; step: number }
  | { mode: 'values'; values: (number | boolean)[] }

export interface BacktestParameterDefinition {
  key: string
  title: string
  group: 'signal' | 'exit'
  value_type: 'integer' | 'number' | 'boolean'
  minimum: number | null
  maximum: number | null
  default: ParameterSpec
  description: string
}

export interface BacktestCatalog {
  version: string
  max_combinations: number
  long_running_warning_combinations: number
  parameters: BacktestParameterDefinition[]
  conditions: {
    id: string
    version: string
    title: string
    default_enabled: boolean
    production_ready: boolean
    status: string
    dataset?: { available: boolean; rows: number; codes?: number; earliest?: string; latest?: string }
  }[]
  research_boundary: string
  paper_trading_enabled: false
  live_trading_enabled: false
}

export interface BacktestUniverseCatalog {
  classification: ClassificationKey
  classification_title: string
  group_label: string
  classification_mode: string
  classification_note: string
  classifications: (Omit<ClassificationDefinition, 'available' | 'coverage_pct' | 'examples'> & { group_count: number })[]
  groups: { name: string; count: number }[]
  industries: { name: string; count: number }[]
  stocks: { ts_code: string; name: string; industry: string; market: string; area: string }[]
  stock_count: number
}

export interface BacktestRequest {
  strategy: 'A'
  sample_step: number
  max_codes: number
  parameters: Record<string, ParameterSpec>
  universe: {
    classification: ClassificationKey
    groups: string[]
    codes: string[]
    industries?: string[]
  }
  conditions: { id: string; enabled: boolean; params?: Record<string, number> }[]
  windows?: { mode: 'auto' }
}

export interface PreparedBacktestRequest extends Omit<BacktestRequest, 'windows'> {
  contract_version: string
  parameter_space: {
    count: number
    sha256: string
    horizon: number
    signal_group_count: number
    exit_group_count: number
    invalid_signal_combinations: number
    long_running: boolean
    long_running_warning_combinations: number
  }
  universe: BacktestRequest['universe'] & {
    source: string
    count: number
    sha256: string
    classification_mode: string
    classification_note: string
    classification_title: string
    group_label: string
  }
  windows: {
    mode: string
    label?: string
    is: [string, string]
    oos: [string, string]
    wf: { train_start: string; train_end: string; test_start: string; test_end: string }[]
    n_dates: number
    earliest?: string
    latest?: string
  }
  input_hash: string
}

export interface BacktestPreview {
  can_run: boolean
  prepared: PreparedBacktestRequest
  estimated_work: {
    combinations: number
    stocks: number
    sample_step: number
    long_running: boolean
    warning_threshold: number
    note: string
  }
}

export interface ManualStrategyParameters {
  box_min_days: number
  box_max_days: number
  box_max_amp: number
  breakout_vol_ratio: number
  breakout_chg_min: number
  breakout_chg_max: number
  breakout_vs_recent_vol_ratio: number
  breakout_window_days: number
  require_structure: boolean
  vol_ratio_min: number
  stop_pct: number
  target_pct: number
  max_hold_days: number
  exit_window: number
  strong_reset: number
}

export interface BacktestMetrics {
  net_n_trades?: number
  net_win_rate?: number | null
  net_avg_return?: number | null
  net_total_return?: number | null
  net_profit_factor?: number | null
  net_max_drawdown?: number | null
  portfolio_status?: string | null
  portfolio_total_return?: number | null
  portfolio_max_drawdown?: number | null
  evidence_complete?: boolean
}

export interface BacktestLeaderboardRow {
  param_id: string
  equivalent_parameter_count?: number
  signal: Record<string, number | boolean>
  exit: Record<string, number>
  is: BacktestMetrics
  oos: BacktestMetrics
}

export interface BacktestWalkForwardWindow {
  window?: string
  train_pf?: number | null
  test_pf?: number | null
  test_dd?: number | null
  test_wr?: number | null
  test_n?: number | null
  train_portfolio_status?: string | null
  test_portfolio_status?: string | null
}

export interface BacktestResult {
  verdict: string
  verdict_label: string
  verdict_reasons?: string[]
  candidate_eligible: false
  can_claim_edge: false
  request: PreparedBacktestRequest
  leaderboard: BacktestLeaderboardRow[]
  independent_leaderboard?: BacktestLeaderboardRow[]
  selected: BacktestLeaderboardRow | null
  evaluated_combinations?: number
  path_analysis?: {
    method: string
    evidence_complete: boolean
    coverage_complete?: boolean
    nominal_combinations: number
    path_eligible_combinations?: number
    excluded_without_complete_path?: number
    independent_is_paths: number | null
    independent_oos_paths: number | null
    independent_joint_paths: number | null
    duplicate_group_count: number
  }
  wf: {
    evidence_complete?: boolean
    wf_pass?: boolean
    train_mean_pf?: number | null
    oos_mean_pf?: number | null
    wf_detail?: BacktestWalkForwardWindow[]
  } | null
  baselines: Record<string, BacktestMetrics & { baseline?: string }> | null
  cost_stress: { multiplier: string; metrics: BacktestMetrics } | null
  warnings: string[]
  report_markdown?: string
}

export interface BacktestTask {
  task_id: string
  research_run_id: string
  research_mode: string
  status: 'pending' | 'running' | 'cancelling' | 'done' | 'error' | 'cancelled' | 'interrupted'
  phase: string
  progress: number
  message: string
  request: PreparedBacktestRequest
  result: BacktestResult | null
  created_at: string
  started_at?: string | null
  updated_at: string
  heartbeat_at?: string | null
  code_version: string
  dataset_version: string
  input_hash: string
  profile_activation?: ProfileActivation
}

export interface StrategyProfileSnapshot {
  profile_id: string
  name: string
  schema_version: number
  version: string
  status: string
  box_min_days: number
  box_max_days: number
  box_max_amp: number
  breakout_vol_ratio: number
  breakout_chg_min: number
  breakout_chg_max: number
  breakout_vs_recent_vol_ratio: number
  breakout_window_days: number
  require_structure: boolean
  vol_ratio_min: number
  strong_reset: number
  exit_window: number
  stop_pct: number
  target_pct: number
  max_hold_days: number
  source_kind: string
  source_task_id?: string | null
}

export interface StrategyProfileView {
  profile_id: string
  name: string
  version: string
  schema_version: number
  is_default: boolean
  status: string
  storage_status: string
  config_hash: string
  activated_at?: string | null
  entry: Record<string, number | boolean>
  exit_reference: Record<string, number>
  required_scan_days: number
  source: {
    kind: string
    task_id?: string | null
    param_id?: string | null
    code_version?: string | null
    dataset_version?: string | null
    input_hash?: string | null
    evidence?: Record<string, unknown>
  }
  notes: string[]
}

export interface StrategyProfileBoundary {
  scope: string
  manual_activation_required: boolean
  automatic_promotion: boolean
  b_pool_uses_profile: boolean
  daily_extra_gates: string[]
  notice: string
}

export interface StrategyProfileState {
  active: StrategyProfileView
  history: StrategyProfileView[]
  boundary: StrategyProfileBoundary
  live_trading_enabled: false
}

export interface ProfileActivationCheck {
  code: string
  label: string
  passed: boolean
  message: string
}

export interface ProfileActivation {
  task_id?: string | null
  can_activate: boolean
  already_active: boolean
  checks: ProfileActivationCheck[]
  reasons: ProfileActivationCheck[]
  boundary: StrategyProfileBoundary
}

// ── AI 证据评测 ──
export interface AIReviewEvidence {
  code: string
  label: string
  value?: string
  as_of?: string
}

export interface ExternalAIInsight {
  ts_code: string
  signal_date: string
  provider: string
  ai_text: string
  created_at?: string
  cached?: boolean
}

export interface AIReview {
  ts_code: string
  name: string
  industry: string
  verdict: 'SUPPORTS_MONITORING' | 'MIXED_EVIDENCE' | 'INSUFFICIENT_EVIDENCE'
  verdict_label: string
  as_of: string | null
  signal_date: string | null
  evidence: AIReviewEvidence[]
  risks: AIReviewEvidence[]
  data: {
    close: number | null
    pe: number | null
    pb: number | null
    roe: number | null
    box_high: number | null
    box_days: number | null
    breakout_vol_ratio: number | null
  }
  external_ai: ExternalAIInsight | null
  generation: {
    available: boolean
    provider: 'deepseek'
    message: string
  }
  boundary: {
    read_only: true
    changes_scan_or_signal: false
    triggers_order: false
    message: string
  }
}
