/** v2 六形态策略类型（P7.3）：与 /api/v2/strategies 后端响应对齐。 */

export interface StrategyInfo {
  strategy_definition_id: string
  version: string
  research_status: string
  economic_assumption: string
  failure_conditions: string
  config_path?: string
  strategy_hash: string
}

export interface StrategyVersion {
  strategy_definition_id: string
  version: string
  economic_assumption: string
  failure_conditions: string
  pit_test?: string
  research_status: string
  strategy_hash: string
}

export interface StrategyRegistry {
  strategies: StrategyInfo[]
  count: number
}
