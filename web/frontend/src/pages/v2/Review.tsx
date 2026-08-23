/**
 * v2 复核页：复核笔记与决策（只读列表）。
 */
import { useEffect, useState } from 'react'
import { fetchNotes, fetchDecisions, type ReviewNote, type ReviewDecision } from '../../api/review'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'

export default function Review() {
  const [notes, setNotes] = useState<ReviewNote[]>([])
  const [decisions, setDecisions] = useState<ReviewDecision[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    Promise.all([fetchNotes({}), fetchDecisions({})])
      .then(([n, d]) => {
        if (!alive) return
        setNotes(Array.isArray(n) ? n : (n as { items?: ReviewNote[] }).items || [])
        setDecisions(Array.isArray(d) ? d : (d as { items?: ReviewDecision[] }).items || [])
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <div className="p-8 text-slate-500">加载复核…</div>
  if (error) return <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: true, status: 0 }} />

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">复核</h1>
      <p className="text-sm text-slate-500 mb-6">复核笔记与决策 · 只读</p>

      <div className="rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">决策</h2>
        {decisions.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">暂无决策</p>
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {decisions.map((d) => (
              <li key={d.decision_id} className="flex items-center justify-between">
                <span className="font-medium">{d.action}</span>
                <span className="text-xs text-slate-400">{d.decided_at}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-semibold">笔记</h2>
        {notes.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">暂无笔记</p>
        ) : (
          <ul className="mt-3 space-y-3">
            {notes.map((n) => (
              <li key={n.note_id} className="text-sm">
                <div className="font-medium">{n.title}</div>
                {n.body && <div className="text-slate-600 mt-1">{n.body}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
