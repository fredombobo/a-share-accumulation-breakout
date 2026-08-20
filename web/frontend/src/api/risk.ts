import { paperWrite, request, V2_BASE } from './core'
import type { PortfolioRisk, StressResult } from '../types/risk'

export async function fetchPortfolioRisk(params?: {
  cashWeight?: number
  weights?: string
}): Promise<PortfolioRisk> {
  const q = new URLSearchParams()
  if (params?.cashWeight != null) q.set('cash_weight', String(params.cashWeight))
  if (params?.weights) q.set('weights', params.weights)
  const s = q.toString()
  return request<PortfolioRisk>(`${V2_BASE}/portfolio/risk${s ? `?${s}` : ''}`)
}

/** 只读压力计算（side_effects=false，不改账本）。 */
export async function runStress(scenarioNames?: string[]): Promise<StressResult> {
  return paperWrite(`${V2_BASE}/portfolio/stress`, {
    scenario_names: scenarioNames ?? null,
  })
}
