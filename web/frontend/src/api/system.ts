import { paperWrite, request, V2_BASE } from './core'
import type { AlertItem, AuditEvent, BackupsStatus, SystemHealth } from '../types/system'

export async function fetchSystemHealth(): Promise<SystemHealth> {
  return request<SystemHealth>(`${V2_BASE}/system/health`)
}

export async function fetchBackups(): Promise<BackupsStatus> {
  return request<BackupsStatus>(`${V2_BASE}/system/backups`)
}

export async function fetchAudit(limit = 100): Promise<{ events: AuditEvent[]; count: number }> {
  return request(`${V2_BASE}/system/audit?limit=${limit}`)
}

export async function fetchAlerts(limit = 50): Promise<{ items: AlertItem[]; count: number }> {
  return request(`${V2_BASE}/alerts?limit=${limit}`)
}

export async function markAlertRead(alertId: string): Promise<AlertItem> {
  return paperWrite(`${V2_BASE}/alerts/${alertId}/read`, {})
}
