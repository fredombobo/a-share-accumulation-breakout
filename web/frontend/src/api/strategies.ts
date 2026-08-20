import { request, V2_BASE } from './core'
import type { StrategyInfo, StrategyVersion } from '../types/strategies'

export async function fetchStrategies(): Promise<StrategyInfo[]> {
  return request<StrategyInfo[]>(`${V2_BASE}/strategies`)
}

export async function fetchStrategyVersions(
  strategyId: string,
): Promise<StrategyVersion[]> {
  return request<StrategyVersion[]>(
    `${V2_BASE}/strategies/${strategyId}/versions`,
  )
}
