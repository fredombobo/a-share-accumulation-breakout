/**
 * v2 六形态策略页面（P7.3）：registry 与研究状态（只读）。
 */
import { useEffect, useState } from 'react'
import { fetchStrategies } from '../../api/strategies'
import type { StrategyInfo } from '../../types/strategies'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'

const STATUS_STYLE: Record<string, string> = {
  EXPERIMENTAL: 'bg-slate-100 text-slate-600',
  CANDIDATE: 'bg-amber-50 text-amber-700',
  SHADOW: 'bg-sky-50 text-sky-700',
  ACTIVE_FOR_A_POOL: 'bg-emerald-50 text-emerald-700',
  REJECTED: 'bg-rose-50 text-rose-700',
  RETIRED: 'bg-slate-100 text-slate-400',
}

export default function Strategies() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchStrategies()
      .then((list) => alive && setStrategies(list))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <div className="p-8 text-slate-500">加载策略注册表…</div>
  if (error) return <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: false, status: 0 }} />

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">六形态策略</h1>
      <p className="text-sm text-slate-500 mb-6">
        唯一策略插件契约 · 共同引用 NEXT_TRADABLE_OPEN_EXECUTION_V1
      </p>
      {strategies.length === 0 ? (
        <EmptyState title="策略注册表为空" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {strategies.map((s) => (
            <div key={s.strategy_definition_id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm font-medium">{s.strategy_definition_id}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[s.research_status] || 'bg-slate-100'}`}>
                  {s.research_status}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">v{s.version}</p>
              <p className="text-sm text-slate-600 mt-2 line-clamp-3">{s.economic_assumption}</p>
              <p className="text-xs text-rose-500 mt-2 line-clamp-2">失效: {s.failure_conditions}</p>
              <p className="text-[11px] font-mono text-slate-300 mt-2 break-all">{s.strategy_hash}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
