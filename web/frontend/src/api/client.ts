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
  status: 'pending' | 'running' | 'done' | 'error' | 'cancelled' | 'idle'
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
}
