/**
 * 平台状态 typed client：服务端 resolved flags / config / build / readiness。
 * 业务旗标一律以服务端下发为准，前端不本地覆盖。
 */
import { request, V2_BASE } from './core'

export interface PlatformStatus {
  status: string
  product: 'accumulation_breakout'
  display_name: string
  default_port: number
  flags: Record<string, boolean>
  config_hash: string
  build_version: string
  live: boolean
  live_trading_enabled: false
  hard_gates: Record<string, boolean>
  readiness: ReadinessVerdict
  readiness_detail: {
    blocked_gates: string[]
    identity_blockers: string[]
    per_gate: Record<string, boolean>
  }
}

export type ReadinessVerdict =
  | 'BLOCKED'
  | 'ENGINEERING_READY_RESEARCH_BLOCKED'
  | 'PERSONAL_INSTITUTIONAL_READY'

export interface GateEvidence {
  gate: string
  status: 'PASS' | 'FAIL' | 'INSUFFICIENT'
  passed: boolean
  source: string
  reason: string
  identity_matches?: boolean
}

export interface ReadinessStatus {
  status: ReadinessVerdict
  gates: Record<string, GateEvidence>
  per_gate: Record<string, boolean>
  blocked_gates: string[]
  identity_blockers: string[]
  identity: Record<string, unknown>
  live_trading_enabled: false
}

export async function fetchPlatformStatus(): Promise<PlatformStatus> {
  return request<PlatformStatus>(`${V2_BASE}/platform/status`)
}

export async function fetchReadiness(): Promise<ReadinessStatus> {
  return request<ReadinessStatus>(`${V2_BASE}/readiness`)
}
