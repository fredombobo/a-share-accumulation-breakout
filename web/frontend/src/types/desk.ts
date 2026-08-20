import type { SideEffectsMarker } from './common'

/** 指挥舱：服务端推导的唯一下一动作（P7.3 Desk 页）。 */
export interface DeskGuide extends SideEffectsMarker {
  next_action: string
  title: string
  reason: string
  primary_label: string
  href: string | null
  trade_date?: string
  latest_market_date?: string | null
  expected_market_date?: string | null
  blocker_codes?: string[]
  task_id?: string
  task_status?: string
  scan_run_id?: string
  reconciliation_id?: number
  order_id?: string
  cycle_id?: string
}

export const DESK_ACTIONS = [
  'SYNC_DATA',
  'WAIT_SCAN',
  'RUN_SCAN',
  'CREATE_ACCOUNT',
  'RESOLVE_RECONCILIATION',
  'REVIEW_DRAFT',
  'RUN_SETTLEMENT',
  'DAILY_COMPLETE',
] as const

export type DeskAction = (typeof DESK_ACTIONS)[number]
