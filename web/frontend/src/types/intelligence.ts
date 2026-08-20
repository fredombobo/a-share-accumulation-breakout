import type { Timed } from './common'

export interface StockSearchHit {
  ts_code: string
  name: string
  industry: string | null
  list_date: string | null
}

export interface InstrumentProfile {
  ts_code?: string
  name?: string
  industry?: string
  list_date?: string | null
  delist_date?: string | null
}

export interface LatestBar extends Timed {
  trade_date?: string
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  vol?: number | null
  amount?: number | null
  pct_chg?: number | null
}

export interface StockProfile {
  instrument: InstrumentProfile | null
  latest_bar: LatestBar | null
}

export interface TimelineEvent extends Timed {
  event_id: string
  event_type: string
  ts_code: string
  title: string
  occurred_at?: string
}

export interface Breadth {
  trade_date: string
  advances: number
  declines: number
  unchanged: number
  total: number
  advance_ratio?: number | null
  advance_decline_ratio?: number | null
}

export interface LimitUpItem {
  ts_code: string
  pct_chg: number
  board_limit_pct: number
}

export interface LimitUpLadder {
  trade_date: string
  status: string
  reason: string | null
  limit_up: number
  limit_down: number
  items: LimitUpItem[]
}

export interface IndexItem {
  ts_code: string
  name: string
  close: number
  pct_chg: number | null
}

export interface IndexSnapshot {
  trade_date: string
  status: string
  reason: string | null
  items: IndexItem[]
  coverage?: number
}

export interface AstockStatus {
  enabled: boolean
  reachable: boolean
  base_url: string
  global: unknown | null
  error: string | null
  service?: string
}

export interface DeskSupplement {
  side_effects: boolean
  not_a_pool: boolean
  trade_date: string | null
  status: string
  reason: string | null
  breadth?: Breadth | null
  limit_up?: LimitUpLadder | null
  indices?: IndexSnapshot | null
  astock?: AstockStatus | null
  disclaimer: string
}

export interface ActiveCoverage {
  total: number
  covered_latest: number
  pct: number
}

export interface DataSourceStatus {
  datasets: Record<
    string,
    { partitions: number; rows: number; last_ingested_at?: string | null }
  >
  daily_latest_trade_date?: string | null
  active_stock_coverage?: ActiveCoverage | null
}

export interface EventCalendarItem extends Timed {
  event_id: string
  event_type: string
  ts_code: string
  title: string
  ann_date?: string
}
