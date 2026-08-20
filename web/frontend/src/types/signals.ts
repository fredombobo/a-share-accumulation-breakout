import type { Timed } from './common'

export interface SignalObservation extends Timed {
  observation_id: string
  strategy_definition_id: string
  strategy_hash?: string
  input_hash?: string
  snapshot_id: string
  ts_code: string
  signal_date: string
  config_hash?: string
  explanation?: string
  tradeable: boolean
  entry_definition_id?: string
  status?: string
}

export type OutcomeStatus = 'PENDING' | 'MATURED' | 'UNFILLABLE' | 'EXPIRED'

export interface SignalOutcome {
  outcome_id: string
  observation_id: string
  horizon_days: 5 | 10 | 20
  revision: number
  status: OutcomeStatus
  entry_price_micro?: number | null
  exit_price_micro?: number | null
  net_return?: number | null
  benchmark_excess?: number | null
  available_at: string
}
