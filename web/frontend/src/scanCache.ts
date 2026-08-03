/**
 * 扫描结果本地缓存：进详情再返回、刷新页面，在「再次扫描成功」前保留上次列表。
 */
import type { OverviewResp, OverviewItem } from './api/client'

const KEY = 'ab_screener_last_overview_v1'
const KEY_POOL = 'ab_screener_pool_v1'
const KEY_PARAMS = 'ab_screener_params_v1'

/** 缓存瘦身上限：ALL 池全量 kline 可能超 sessionStorage 配额，这里做容量封顶。 */
const MAX_ITEMS = 40
const MAX_KLINE_POINTS = 120

export type CachedOverview = {
  savedAt: string
  pool: 'A' | 'B' | 'ALL'
  data: OverviewResp
}

/** 只保留总览页所需的摘要字段：丢弃 fina（仅详情页用），kline 截断至箱体窗口。 */
function slimItem(it: OverviewItem): OverviewItem {
  const copy: OverviewItem = { ...it }
  delete copy.fina
  if (Array.isArray(copy.kline) && copy.kline.length > MAX_KLINE_POINTS) {
    copy.kline = copy.kline.slice(-MAX_KLINE_POINTS)
  }
  return copy
}

function slimOverview(data: OverviewResp): OverviewResp {
  const items = (data.items || []).slice(0, MAX_ITEMS).map(slimItem)
  return { ...data, items, count: items.length }
}

export function saveOverviewCache(pool: 'A' | 'B' | 'ALL', data: OverviewResp): void {
  try {
    const payload: CachedOverview = {
      savedAt: new Date().toISOString(),
      pool,
      data: slimOverview(data),
    }
    sessionStorage.setItem(KEY, JSON.stringify(payload))
    sessionStorage.setItem(KEY_POOL, pool)
  } catch {
    /* quota / private mode */
  }
}

export function loadOverviewCache(): CachedOverview | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as CachedOverview
    if (!parsed?.data || !Array.isArray(parsed.data.items)) return null
    return parsed
  } catch {
    return null
  }
}

export function loadPoolPref(): 'A' | 'B' | 'ALL' | null {
  try {
    const p = sessionStorage.getItem(KEY_POOL)
    if (p === 'A' || p === 'B' || p === 'ALL') return p
  } catch {
    /* ignore */
  }
  return null
}

export function saveParams(topN: number, days: number): void {
  try {
    sessionStorage.setItem(KEY_PARAMS, JSON.stringify({ topN, days }))
  } catch {
    /* ignore */
  }
}

export function loadParams(): { topN: number; days: number } | null {
  try {
    const raw = sessionStorage.getItem(KEY_PARAMS)
    if (!raw) return null
    const p = JSON.parse(raw)
    if (typeof p?.topN === 'number' && typeof p?.days === 'number') return p
  } catch {
    /* ignore */
  }
  return null
}
