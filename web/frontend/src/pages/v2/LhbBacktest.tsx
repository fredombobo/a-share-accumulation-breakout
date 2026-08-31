import { useState } from 'react'
import { fetchLhbBacktest, fetchLhbSignals } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { StatusStrip } from '../../components/common/StatusStrip'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbSignal, LhbSourceStatus } from '../../types/lhb'

export default function LhbBacktest() {
  const [summary, setSummary] = useState<LhbEnvelope<Record<string, unknown>> | null>(null)
  const [signals, setSignals] = useState<LhbEnvelope<LhbSignal> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      setSummary(await fetchLhbBacktest())
      setSignals(await fetchLhbSignals())
      return null
    })

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">回测与 Shadow</h1>
      <p className="text-sm text-slate-500 mb-4">工程完成 ≠ 存在 edge · 默认 RESEARCH_BLOCKED</p>
      <button type="button" onClick={load} className="mb-4 rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white">
        刷新
      </button>
      {error && <ApiErrorPanel error={error} onRetry={load} />}
      {summary && (
        <LhbSourceBanner
          status={summary.source_status as LhbSourceStatus}
          asOf={summary.as_of}
          errorReason={summary.error_reason}
        />
      )}
      {summary && (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <StatusStrip tone="warn" label={String(summary.research_status || 'RESEARCH_BLOCKED')} />
          <div className="mt-2">可声称 edge：{summary.can_claim_edge ? '是' : '否'}</div>
          <div>工程 PASS 不是研究 PASS</div>
        </div>
      )}
      {signals && signals.items.length === 0 ? (
        <EmptyState title="无 shadow 观察" hint="未过样本外门禁前不进入 A 池" />
      ) : signals ? (
        <table className="w-full text-sm border border-slate-200 rounded-lg bg-white">
          <thead>
            <tr className="text-left text-slate-400 border-b">
              <th className="px-3 py-2">日期</th>
              <th className="px-3 py-2">代码</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">最早可执行</th>
            </tr>
          </thead>
          <tbody>
            {signals.items.map((s) => (
              <tr key={s.observation_id} className="border-b border-slate-50">
                <td className="px-3 py-2 font-mono text-xs">{s.signal_date}</td>
                <td className="px-3 py-2 font-mono text-xs">{s.ts_code}</td>
                <td className="px-3 py-2">{s.status}</td>
                <td className="px-3 py-2 font-mono text-xs">{s.earliest_executable_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}
