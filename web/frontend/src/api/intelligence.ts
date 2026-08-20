import { request, V2_BASE } from './core'
import type {
  Breadth,
  DataSourceStatus,
  DeskSupplement,
  StockProfile,
  StockSearchHit,
  TimelineEvent,
} from '../types/intelligence'

export async function searchStocks(q: string): Promise<StockSearchHit[]> {
  return request<StockSearchHit[]>(
    `${V2_BASE}/intelligence/search?q=${encodeURIComponent(q)}`,
  )
}

export async function fetchStockProfile(
  tsCode: string,
  decisionAt?: string,
): Promise<StockProfile> {
  const q = decisionAt ? `?decision_at=${encodeURIComponent(decisionAt)}` : ''
  return request<StockProfile>(`${V2_BASE}/intelligence/stocks/${tsCode}${q}`)
}

export async function fetchStockTimeline(tsCode: string): Promise<{
  ts_code: string
  events: TimelineEvent[]
  count: number
}> {
  return request(`${V2_BASE}/intelligence/stocks/${tsCode}/timeline`)
}

export async function fetchBreadth(tradeDate: string): Promise<Breadth> {
  return request<Breadth>(
    `${V2_BASE}/intelligence/breadth?trade_date=${encodeURIComponent(tradeDate)}`,
  )
}

export async function fetchDataStatus(): Promise<DataSourceStatus> {
  return request<DataSourceStatus>(`${V2_BASE}/intelligence/data-status`)
}

export async function fetchDeskSupplement(
  tradeDate?: string,
): Promise<DeskSupplement> {
  const q = tradeDate
    ? `?trade_date=${encodeURIComponent(tradeDate)}`
    : ''
  return request<DeskSupplement>(`${V2_BASE}/intelligence/desk-supplement${q}`)
}
