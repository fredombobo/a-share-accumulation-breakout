import type { SideEffectsMarker } from './common'

export interface PaperStatus extends SideEffectsMarker {
  account_exists: boolean
  account_id?: number
  cash_fen?: number
  cash_cny?: string | null
  status?: string
}
