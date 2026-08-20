import type { SideEffectsMarker } from './common'

export interface PortfolioRisk extends SideEffectsMarker {
  status?: string
  metrics?: Record<string, unknown>
  positions_exposure?: unknown[]
  liquidity_days?: number | null
  insufficient: string[]
  scenarios?: Record<string, unknown>
}

export interface StressScenario {
  name: string
  pnl_fen?: number
  pnl_pct?: number | null
  detail?: string
}

export interface StressResult extends SideEffectsMarker {
  scenarios: StressScenario[]
}
