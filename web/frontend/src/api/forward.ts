import { request, type ReqOpts } from './client'

export interface ForwardStatus {
  enabled: boolean
  protocol: { version: string; enabled_at: string; horizons: number[]; note: string } | null
  counts: { captures: number; primary: number; secondary: number; candidates: number; outcomes: number }
  last_attempt: { status: string; message?: string; occurred_at: string } | null
  last_error: { message?: string; reason?: string; occurred_at: string; run_id?: string } | null
  uncaptured_runs?: string[]
  missing_primary_runs?: string[]
  note: string
}
export interface ForwardCapture {
  run_id: string
  as_of: string
  captured_at: string
  entry_hash: string
  config_hash: string
  role: 'PRIMARY' | 'SECONDARY'
  primary_run_id?: string
  counts: { A: number; B: number; DATA_INCOMPLETE: number; total: number }
}
export interface ForwardOutcome {
  run_id: string
  ts_code: string
  role: 'PRIMARY' | 'SECONDARY'
  pool: string
  group: string
  as_of: string
  target_date: string | null
  horizon: number
  status: string
  raw_return: number | null
  adjusted_return: number | null
  revision: number
  computed_at: string
  base_quote?: { date: string; close: number | null }
  target_quote?: { date: string; close: number | null }
  input_hash?: string
  observation_basis?: string
  baseline_snapshot_hash?: string
  baseline_captured_at?: string
  restated?: { basis: string; raw_return: number | null; adjusted_return: number | null; status: string; differs_from_capture: boolean } | null
  lineage?: { previous_revision: number | null; previous_input_hash: string | null; baseline_snapshot_hash: string; baseline_captured_at: string }
}
export const forwardApi = {
  status: (opts?: ReqOpts) => request<ForwardStatus>('/forward/status', opts),
  enable: () => request<ForwardStatus>('/forward/enable', { method: 'POST' }),
  history: (offset = 0, opts?: ReqOpts) => request<{ items: ForwardCapture[]; total: number }>(`/forward/history?limit=50&offset=${offset}`, opts),
  capture: (runId: string) => request<{ status: string; captured: boolean; message?: string }>('/forward/capture', { method: 'POST', body: JSON.stringify({ run_id: runId }) }),
  refresh: (runId?: string) => request<{ status: string; appended: number; message?: string }>('/forward/refresh', { method: 'POST', timeoutMs: 120000, body: JSON.stringify({ run_id: runId }) }),
  results: (runId: string, horizon: number, offset: number, allRevisions: boolean, opts?: ReqOpts) =>
    request<{ items: ForwardOutcome[]; total: number; note: string }>(`/forward/results?run_id=${encodeURIComponent(runId)}&horizon=${horizon}&offset=${offset}&limit=100&all_revisions=${allRevisions}`, opts),
}
