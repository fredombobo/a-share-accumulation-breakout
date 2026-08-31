import { request, V2_BASE } from './core'
import type { LhbEnvelope, LhbEvent, LhbNetworkEdge, LhbProfile, LhbSignal } from '../types/lhb'

const LHB = `${V2_BASE}/lhb`

export function formatYuan(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `${n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元`
}

export function defaultTradeDate(): string {
  const date = new Date()
  if (date.getHours() < 18) date.setDate(date.getDate() - 1)
  while (date.getDay() === 0 || date.getDay() === 6) date.setDate(date.getDate() - 1)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}${month}${day}`
}

export async function fetchLhbRadar(tradeDate: string): Promise<LhbEnvelope<LhbEvent>> {
  return request(`${LHB}/radar?trade_date=${encodeURIComponent(tradeDate)}`)
}

export async function fetchLhbEvents(params: {
  tradeDate?: string
  tsCode?: string
}): Promise<LhbEnvelope<LhbEvent>> {
  const q = new URLSearchParams()
  if (params.tradeDate) q.set('trade_date', params.tradeDate)
  if (params.tsCode) q.set('ts_code', params.tsCode)
  const suffix = q.toString() ? `?${q.toString()}` : ''
  return request(`${LHB}/events${suffix}`)
}

export async function fetchLhbSeat(seatId: string, asOf: string): Promise<LhbEnvelope<LhbProfile>> {
  return request(`${LHB}/seats/${encodeURIComponent(seatId)}?as_of=${encodeURIComponent(asOf)}`)
}

export async function fetchLhbActor(actorId: string, asOf: string): Promise<LhbEnvelope<LhbProfile>> {
  return request(`${LHB}/actors/${encodeURIComponent(actorId)}?as_of=${encodeURIComponent(asOf)}`)
}

export async function fetchLhbTimeline(tsCode: string): Promise<LhbEnvelope<LhbEvent>> {
  return request(`${LHB}/stocks/${encodeURIComponent(tsCode)}/timeline`)
}

export async function fetchLhbNetwork(tradeDate: string): Promise<LhbEnvelope<LhbNetworkEdge>> {
  return request(`${LHB}/network?trade_date=${encodeURIComponent(tradeDate)}`)
}

export async function fetchLhbQuality(tradeDate: string): Promise<LhbEnvelope<Record<string, unknown>>> {
  return request(`${LHB}/quality?trade_date=${encodeURIComponent(tradeDate)}`)
}

export async function fetchLhbSignals(status?: string): Promise<LhbEnvelope<LhbSignal>> {
  const q = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`${LHB}/signals${q}`)
}

export async function fetchLhbBacktest(): Promise<LhbEnvelope<Record<string, unknown>>> {
  return request(`${LHB}/backtest`)
}
