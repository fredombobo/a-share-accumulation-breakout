export interface DeepCheckStatus {
  status: 'PASS' | 'STALE' | 'MISSING' | 'FAIL'
  reason?: string
  finished_at?: string
  duration_sec?: number
  tables?: number
}

export interface DatabaseHealth {
  ok: boolean
  reason?: string
  size_bytes?: number
  wal_bytes?: number
  fingerprint?: string
  schema_version?: string | null
  latest_date?: string | null
  deep_check?: DeepCheckStatus
}

export interface DiskStatus {
  free_gb?: number
  ok?: boolean
}

export interface SystemHealth {
  status: 'PASS' | 'FAIL'
  issues?: string[]
  build_version?: string
  config_hash?: string
  port?: number
  database?: DatabaseHealth
  disk?: DiskStatus
  backup?: Record<string, unknown>
  checked_at?: string
}

export interface BackupInfo {
  path?: string
  size_mb?: number
  created_at?: string
  sha256?: string
}

export interface BackupsStatus {
  backup_root: string
  latest: BackupInfo | null
  status: Record<string, unknown>
}

export interface AuditEvent {
  event_id: string
  actor: string
  action: string
  correlation_id?: string
  event_hash: string
  prev_hash?: string
  occurred_at: string
}

export interface AlertItem {
  alert_id: string
  severity: string
  title: string
  body?: string
  read: boolean
  created_at?: string
}
