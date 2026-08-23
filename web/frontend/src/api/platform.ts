/**
 * 平台状态 typed client：服务端 resolved flags / config / build / readiness。
 * 业务旗标一律以服务端下发为准，前端不本地覆盖。
 */
import { request, V2_BASE } from './core'

export interface PlatformStatus {
  status: string
  flags: Record<string, boolean>
  config_hash: string
  build_version: string
  live: boolean
  readiness: string | null
}

export interface ReadinessStatus {
  status: string
  gates: Record<string, string>
  identity_ok: boolean
  worktree_clean: boolean
}

export async function fetchPlatformStatus(): Promise<PlatformStatus> {
  return request<PlatformStatus>(`${V2_BASE}/platform/status`)
}

export async function fetchReadiness(): Promise<ReadinessStatus> {
  return request<ReadinessStatus>(`${V2_BASE}/readiness`)
}
