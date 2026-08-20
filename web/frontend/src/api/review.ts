import { paperWrite, request, V2_BASE } from './core'
import type { Paginated } from '../types/common'

export interface ReviewNote {
  note_id: string
  ref_type: string
  ref_id: string | null
  kind: string
  title: string
  body: string
  tags: string[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface ReviewDecision {
  decision_id: string
  ref_type: string
  ref_id: string | null
  action: string
  rationale: string
  risk_flags: string[]
  created_by: string
  decided_at: string
}

export interface WeeklyReport {
  since: string | null
  note_count: number
  decision_count: number
  recent_notes: ReviewNote[]
  recent_decisions: ReviewDecision[]
}

export async function fetchNotes(params?: {
  refType?: string
  kind?: string
  limit?: number
}): Promise<Paginated<ReviewNote>> {
  const q = new URLSearchParams()
  if (params?.refType) q.set('ref_type', params.refType)
  if (params?.kind) q.set('kind', params.kind)
  if (params?.limit != null) q.set('limit', String(params.limit))
  const s = q.toString()
  return request(`${V2_BASE}/review/notes${s ? `?${s}` : ''}`)
}

export async function createNote(payload: {
  title: string
  body?: string
  ref_type?: string
  ref_id?: string | null
  kind?: string
  tags?: string[]
}): Promise<ReviewNote> {
  return paperWrite(`${V2_BASE}/review/notes`, payload)
}

export async function fetchDecisions(params?: {
  refType?: string
  limit?: number
}): Promise<Paginated<ReviewDecision>> {
  const q = new URLSearchParams()
  if (params?.refType) q.set('ref_type', params.refType)
  if (params?.limit != null) q.set('limit', String(params.limit))
  const s = q.toString()
  return request(`${V2_BASE}/review/decisions${s ? `?${s}` : ''}`)
}

export async function createDecision(payload: {
  action: string
  rationale: string
  ref_type?: string
  ref_id?: string | null
  risk_flags?: string[]
}): Promise<ReviewDecision> {
  return paperWrite(`${V2_BASE}/review/decisions`, payload)
}

export async function fetchWeekly(since?: string): Promise<WeeklyReport> {
  const q = since ? `?since=${encodeURIComponent(since)}` : ''
  return request(`${V2_BASE}/review/weekly${q}`)
}

export async function fetchAttribution(): Promise<Record<string, unknown>> {
  return request(`${V2_BASE}/review/attribution`)
}
