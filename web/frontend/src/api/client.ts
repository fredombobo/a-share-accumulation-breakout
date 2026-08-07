const BASE = '/api'

/** 默认请求超时：30s，超时后 abort。 */
const DEFAULT_TIMEOUT_MS = 30_000

/** 可传外部 AbortSignal 与自定义超时。 */
export type ReqOpts = { signal?: AbortSignal; timeoutMs?: number }

async function request<T>(path: string, init?: RequestInit & ReqOpts): Promise<T> {
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
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
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
  notes?: string[]
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
  progress?: number
  message?: string
  error?: string | null
  result?: {
    is_top: Record<string, unknown>[]
    is_all?: Record<string, unknown>[]
    oos: Record<string, unknown>[]
    msg?: string
    run_mode?: string
    research_mode?: string
    can_claim_edge?: boolean
    params_used?: unknown
    windows?: LabWindows
  } | null
  strategy?: string
  windows?: LabWindows
}

export interface LabBoardResp {
  rows: Record<string, unknown>[]
  source: string
}

export interface LabCompareResp {
  rows?: Record<string, unknown>[]
  best_by_strategy?: Record<string, Record<string, unknown> | null>
}

export interface LabArenaResp {
  rows: Record<string, unknown>[]
  weights: Record<string, number>
}
