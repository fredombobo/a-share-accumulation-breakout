/**
 * v2 信号观察页面（P7.3）：按形态/状态/日期分页查询（只读）。
 */
import { useEffect, useState } from 'react'
import { request, V2_BASE } from '../../api/core'
import type { SignalObservation } from '../../types/signals'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'

interface SignalRow {
  observation_id: string
  strategy_definition_id: string
  ts_code: string
  signal_date: string
  tradeable: boolean
  entry_definition_id: string
  status: string
}

export default function Signals() {
  const [rows, setRows] = useState<SignalRow[]>([])
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async (filterStatus: string) => {
    try {
      setError('')
      const query = filterStatus ? `?status=${encodeURIComponent(filterStatus)}` : ''
      const data = await request<{ items: SignalRow[] }>(`${V2_BASE}/signals${query}`)
      setRows(data.items)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">信号观察</h1>
      <p className="text-sm text-slate-500 mb-4">不可变观察 · 生命周期投影 · 只读</p>
      <div className="mb-4 flex gap-2">
        {['', 'OBSERVED', 'QUALIFIED', 'TRADEABLE', 'ORDER_CREATED', 'ENTERED'].map((s) => (
          <button
            key={s || 'ALL'}
            onClick={() => {
              setStatus(s)
              load(s)
            }}
            className={`rounded-lg px-3 py-1 text-xs font-medium ${
              status === s ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {s || '全部'}
          </button>
        ))}
      </div>
      {error && <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: false, status: 0 }} />}
      {loading ? (
        <div className="p-8 text-slate-500">加载信号…</div>
      ) : rows.length === 0 ? (
        <EmptyState title="无信号观察" hint="先运行扫描管线" />
      ) : (
        <table className="w-full text-sm border border-slate-200 rounded-lg bg-white">
          <thead>
            <tr className="text-left text-slate-400 border-b border-slate-100">
              <th className="px-3 py-2">日期</th>
              <th className="px-3 py-2">代码</th>
              <th className="px-3 py-2">形态</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">可交易</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.observation_id} className="border-b border-slate-50 hover:bg-slate-50">
                <td className="px-3 py-2 font-mono">{r.signal_date}</td>
                <td className="px-3 py-2 font-mono">{r.ts_code}</td>
                <td className="px-3 py-2 font-mono text-xs">{r.strategy_definition_id}</td>
                <td className="px-3 py-2">
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs">{r.status}</span>
                </td>
                <td className="px-3 py-2">{r.tradeable ? '✓' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
