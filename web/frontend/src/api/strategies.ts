import { request, V2_BASE } from './core'
import type {
  StrategyInfo,
  StrategyRegistry,
  StrategyVersion,
} from '../types/strategies'

export async function fetchStrategies(): Promise<StrategyInfo[]> {
  const registry = await request<StrategyRegistry>(`${V2_BASE}/strategies`)
  if (!Array.isArray(registry.strategies)) {
    throw new Error('策略注册表响应格式无效，请刷新后重试')
  }
  return registry.strategies
}

export async function fetchStrategyVersions(
  strategyId: string,
): Promise<StrategyVersion> {
  return request<StrategyVersion>(
    `${V2_BASE}/strategies/${strategyId}/versions`,
  )
}
