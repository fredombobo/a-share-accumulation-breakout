export interface SystemHealth {
  build?: string
  db_path?: string
  db_size_mb?: number
  wal_size_mb?: number
  disk_free_gb?: number
  dag?: Record<string, unknown>
  backup?: Record<string, unknown>
  ports?: Record<string, unknown>
  errors?: string[]
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
