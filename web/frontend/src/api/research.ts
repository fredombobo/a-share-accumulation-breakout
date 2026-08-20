import { paperWrite, request, V2_BASE } from './core'
import type { Experiment, ResearchRun, StartRunRequest, StartRunResult } from '../types/research'

export async function fetchExperiments(limit = 100): Promise<{ items: Experiment[]; count: number | null }> {
  return request(`${V2_BASE}/research/experiments?limit=${limit}`)
}

export async function registerExperiment(payload: {
  strategy: string
  params: Record<string, unknown>
  config_hash: string
}): Promise<{ experiment_id: string; status: string }> {
  return paperWrite(`${V2_BASE}/research/experiments`, payload)
}

export async function startResearchRun(
  experimentId: string,
  req: StartRunRequest,
): Promise<StartRunResult> {
  return paperWrite(
    `${V2_BASE}/research/experiments/${experimentId}/runs`,
    req,
  )
}

export async function fetchResearchRun(runId: string): Promise<ResearchRun> {
  return request<ResearchRun>(`${V2_BASE}/research/runs/${runId}`)
}

export async function cancelResearchRun(
  runId: string,
): Promise<{ research_run_id: string; status: string }> {
  return paperWrite(`${V2_BASE}/research/runs/${runId}/cancel`, {})
}
