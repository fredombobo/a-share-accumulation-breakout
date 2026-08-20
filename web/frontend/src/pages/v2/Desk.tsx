/**
 * v2 指挥舱页面（P7.3）：今日唯一动作 + 全局摘要。
 * 只读：fetchDesk() side_effects=false。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { fetchDesk } from '../../api/desk'
import { fetchDeskSupplement } from '../../api/intelligence'
import type { DeskGuide } from '../../types/desk'
import type { DeskSupplement } from '../../types/intelligence'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'

export default function Desk() {
  const nav = useNavigate()
  const [guide, setGuide] = useState<DeskGuide | null>(null)
  const [supplement, setSupplement] = useState<DeskSupplement | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchDesk()
      .then((g) => alive && setGuide(g))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false))
    fetchDeskSupplement()
      .then((s) => alive && setSupplement(s))
      .catch(() => {
        /* 情报桥可选，失败静默 */
      })
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <div className="p-8 text-slate-500">加载指挥舱…</div>
  if (error) return <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: false, status: 0 }} />

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">指挥舱</h1>
      <p className="text-sm text-slate-500 mb-6">今日唯一动作 · 服务端推导 · 只读</p>
      {guide && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs font-medium text-indigo-600 uppercase tracking-wide">
                {guide.next_action}
              </div>
              <h2 className="text-xl font-semibold mt-1">{guide.title}</h2>
              <p className="text-sm text-slate-600 mt-2">{guide.reason}</p>
            </div>
            {guide.href && (
              <button
                onClick={() => nav(guide.href!)}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
              >
                {guide.primary_label || '前往'}
              </button>
            )}
          </div>
          <dl className="mt-6 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-400">交易日</dt>
              <dd className="font-mono">{guide.trade_date || '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-400">最新行情</dt>
              <dd className="font-mono">{guide.latest_market_date || '—'}</dd>
            </div>
          </dl>
          {guide.blocker_codes?.length ? (
            <div className="mt-4 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
              阻断: {guide.blocker_codes.join(', ')}
            </div>
          ) : null}
        </div>
      )}

      {supplement?.limit_up && supplement.limit_up.status === 'PASS' && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">今日情报 · 只读 · 非 A 池</span>
            <span>
              <span className="text-red-600 font-semibold">{supplement.limit_up.limit_up}</span>
              {' '}涨停
              <span className="ml-3 text-emerald-600 font-semibold">{supplement.limit_up.limit_down}</span>
              {' '}跌停
            </span>
          </div>
          {supplement.indices && supplement.indices.items.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
              {supplement.indices.items.slice(0, 3).map((it) => (
                <span key={it.ts_code}>
                  {it.name} {it.close}
                  {it.pct_chg != null && (
                    <span className={it.pct_chg >= 0 ? ' text-red-600' : ' text-emerald-600'}>
                      {' '}
                      {it.pct_chg >= 0 ? '+' : ''}
                      {it.pct_chg}%
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
