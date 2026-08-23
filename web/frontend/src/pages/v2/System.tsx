/**
 * v2 系统页：快速健康 / 最后一次深度完整性检查（分开显示）+ 备份。
 */
import { useEffect, useState } from 'react'
import { fetchSystemHealth, fetchBackups } from '../../api/system'
import type { SystemHealth, BackupsStatus } from '../../types/system'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'

export default function System() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [backups, setBackups] = useState<BackupsStatus | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchSystemHealth()
      .then((h) => alive && setHealth(h))
      .catch((e: Error) => alive && setError(e.message))
    fetchBackups()
      .then((b) => alive && setBackups(b))
      .catch(() => {
        /* 备份可选 */
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <div className="p-8 text-slate-500">加载系统状态…</div>
  if (error) return <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: true, status: 0 }} />

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">系统</h1>
      <p className="text-sm text-slate-500 mb-6">快速健康与深度检查分开显示 · 只读</p>

      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">快速健康（热路径，不跑全库完整性检查）</h2>
        {health && (
          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-400">构建</dt>
              <dd className="font-mono">{health.build || '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-400">DB 大小</dt>
              <dd className="font-mono">{health.db_size_mb != null ? `${health.db_size_mb} MB` : '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-400">WAL</dt>
              <dd className="font-mono">{health.wal_size_mb != null ? `${health.wal_size_mb} MB` : '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-400">磁盘剩余</dt>
              <dd className="font-mono">{health.disk_free_gb != null ? `${health.disk_free_gb} GB` : '—'}</dd>
            </div>
          </dl>
        )}
      </section>

      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">最后一次深度完整性检查</h2>
        <p className="mt-2 text-sm text-slate-500">
          深度完整性检查由离线脚本执行（PRAGMA integrity_check），快速健康仅读取最近一次证书。
        </p>
        {health?.errors?.length ? (
          <p className="mt-2 text-sm text-red-600">{health.errors.join('; ')}</p>
        ) : (
          <p className="mt-2 text-sm text-slate-400">暂无深度检查异常记录</p>
        )}
      </section>

      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">备份</h2>
        {backups ? (
          <dl className="mt-3 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-400">备份根</dt>
              <dd className="font-mono">{backups.backup_root}</dd>
            </div>
            <div>
              <dt className="text-slate-400">最新备份</dt>
              <dd className="font-mono">
                {backups.latest?.path ? `${backups.latest.path} (${backups.latest.size_mb} MB)` : '无'}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="mt-2 text-sm text-slate-400">无备份信息</p>
        )}
      </section>
    </div>
  )
}
