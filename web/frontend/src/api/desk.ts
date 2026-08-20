import { request, V2_BASE } from './core'
import type { DeskGuide } from '../types/desk'

/** 今日唯一动作 + 全局摘要（只读）。 */
export async function fetchDesk(): Promise<DeskGuide> {
  return request<DeskGuide>(`${V2_BASE}/desk`)
}
