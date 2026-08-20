import { request, V2_BASE } from './core'
import type { PaperStatus } from '../types/paper'

/** 只读纸面账户状态（受控写请继续使用 legacy /api/paper/*）。 */
export async function fetchPaperStatus(): Promise<PaperStatus> {
  return request<PaperStatus>(`${V2_BASE}/paper/status`)
}
