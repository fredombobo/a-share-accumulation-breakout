import type { Timed } from './common'

export interface Experiment {
  experiment_id: string
  strategy: string
  params: Record<string, unknown>
  config_hash: string
  status: string
  registered_at?: string
}

export interface ResearchRun extends Timed {
  research_run_id: string
  strategy?: string
  research_mode?: string
  can_claim_edge?: boolean
  status: string
  phase?: string
  progress?: number
  message?: string
  input_hash?: string
  dataset_version?: string
  code_version?: string
  cost_version?: string
  created_at?: string
  updated_at?: string
}

export interface StartRunRequest {
  input_hash?: string
  research_mode?: string
  dataset_version?: string
  code_version?: string
  cost_version?: string
}

export interface StartRunResult {
  research_run_id: string
  reused: boolean
}
