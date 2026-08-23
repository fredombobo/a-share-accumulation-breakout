/**
 * v2 监控页：系统健康 + 告警（只读）。
 */
import { useEffect, useState } from 'react'
import { fetchSystemHealth, fetchAlerts } from '../../api/system'
import type { SystemHealth, AlertItem } from '../../types/system'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'

export default function Monitor() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchSystemHealth()
      .then((h) => alive && setHealth(h))
      .catch((e: Error) => alive && setError(e.message))
    fetchAlerts(50)
      .then((r) => alive && setAlerts(r.items || []))
      .catch(() => {
        /* 告警可选 */
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <div className="p-8 text-slate-500">加载监控…</div>
  if (error) return <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: true, status: 0 }} />

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">监控</h1>
      <p className="text-sm text-slate-500 mb-6">系统健康与告警 · 只读</p>

      {health && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <h2 className="font-semibold">系统健康</h2>
          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-400">DB 大小</dt>
              <dd className="font-mono">
                {health.database?.size_bytes != null
                  ? `${(health.database.size_bytes / 1e6).toFixed(1)} MB`
                  : '—'}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">磁盘剩余</dt>
              <dd className="font-mono">{health.disk?.free_gb != null ? `${health.disk.free_gb} GB` : '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-400">WAL</dt>
              <dd className="font-mono">
                {health.database?.wal_bytes != null
                  ? `${(health.database.wal_bytes / 1e6).toFixed(1)} MB`
                  : '—'}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">错误</dt>
              <dd className="font-mono">{health.issues?.length ? health.issues.join('; ') : '无'}</dd>
            </div>
          </dl>
        </div>
      )}

      <div className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">告警</h2>
        {alerts.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">暂无告警</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {alerts.map((a) => (
              <li key={a.alert_id} className="flex items-center justify-between text-sm">
                <span>
                  <span className="font-medium">{a.title}</span>
                  <span className="ml-2 text-xs text-slate-400">{a.severity}</span>
                </span>
                {!a.read && <span className="text-xs text-amber-600">未读</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
