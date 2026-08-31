import type { Timed } from './common'

export type LhbSourceStatus =
  | 'VALID_EMPTY'
  | 'NOT_PUBLISHED'
  | 'FETCH_FAILED'
  | 'DEGRADED'
  | 'COMPLETE'

export type LhbSignalStatus =
  | 'WATCH'
  | 'CONFIRMED_FLOW'
  | 'RESEARCH_ENTRY'
  | 'NO_CHASE'
  | 'INVALIDATED'

export interface LhbEnvelope<T> extends Timed {
  as_of: string
  available_at: string
  source_status: LhbSourceStatus
  amount_unit: 'yuan'
  policy_version: string
  model_version?: string
  research_only: true
  items: T[]
  count: number
  error_reason?: string | null
  ts_code?: string
  subject_id?: string
  can_claim_edge?: boolean
  research_status?: string
  engineering_pass_is_not_edge?: boolean
  nodes?: LhbNetworkNode[]
  independent_actor_count?: number
  method_note?: string
}

export interface LhbNetworkNode {
  actor_id: string
  label: string
  net_yuan: number
  stock_count: number
}

export interface LhbNetworkEdge {
  source_actor_id: string
  target_actor_id: string
  weight: number
  ts_codes: string[]
}

export interface LhbEvent {
  event_id: string
  ts_code: string
  exchange: string
  window_code: string
  reason_code: string
  reason_raw: string
  disclose_date: string
  source_status: LhbSourceStatus
  available_at: string
  payload?: {
    buy_yuan?: number
    sell_yuan?: number
    net_yuan?: number
  }
}

export interface LhbIdentity {
  actor_id: string
  confidence: number
  evidence_grade: 'A' | 'B' | 'C' | string
  note?: string | null
  identity_language: string
}

export interface LhbProfile {
  subject_type: 'seat' | 'actor' | 'stock' | 'board'
  subject_id: string
  window_days: number
  as_of_date?: string
  sample_size: number
  display_win_rate?: number | null
  raw_win_rate?: number | null
  shrunk_win_rate?: number | null
  reliable_100pct_forbidden?: boolean
  buy_yuan?: number
  sell_yuan?: number
  net_yuan?: number
  last_event_date?: string | null
  identity?: LhbIdentity | null
  event_ids?: string[]
}

export interface LhbSignal {
  observation_id: string
  ts_code: string
  signal_date: string
  status: LhbSignalStatus
  disclose_at: string
  earliest_executable_at: string
  policy_version: string
  scores: Record<string, number>
  vetoes: string[]
  research_only: true
}
