import { request, V2_BASE } from './core'
import type { SignalObservation, SignalOutcome } from '../types/signals'

export async function fetchObservation(
  observationId: string,
): Promise<SignalObservation> {
  return request<SignalObservation>(
    `${V2_BASE}/signals/observations/${observationId}`,
  )
}

export async function fetchOutcomes(
  observationId: string,
): Promise<SignalOutcome[]> {
  return request<SignalOutcome[]>(
    `${V2_BASE}/signals/observations/${observationId}/outcomes`,
  )
}
