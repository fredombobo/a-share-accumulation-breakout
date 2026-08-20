/** v2 公共类型（P7.2）：分页 / 时点 / 错误 envelope。 */

export interface Paginated<T> {
  items: T[]
  count: number
  nextCursor?: string | null
}

export interface Timed {
  source?: string
  revision?: string
  effectiveAt?: string
  availableAt?: string
  ingestedAt?: string
}

export interface ApiErrorEnvelope {
  code: string
  message: string
  details?: Record<string, unknown>
  retryable: boolean
  request_id?: string
}

export interface SideEffectsMarker {
  side_effects: false
}
