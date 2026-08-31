import { useState } from 'react'
import { defaultTradeDate, fetchLhbNetwork, formatYuan } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbNetworkEdge, LhbSourceStatus } from '../../types/lhb'

export default function LhbNetwork() {
  const [tradeDate, setTradeDate] = useState(defaultTradeDate)
  const [data, setData] = useState<LhbEnvelope<LhbNetworkEdge> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      setData(await fetchLhbNetwork(tradeDate))
      return null
    })

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">协同席位网络</h1>
      <p className="text-sm text-slate-500 mb-4">同一 actor 多席位只计 1 张独立票</p>
      <div className="mb-4 flex gap-2">
        <input
          value={tradeDate}
          onChange={(e) => setTradeDate(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 font-mono text-sm"
        />
        <button type="button" onClick={load} className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white">
          查询
        </button>
      </div>
      {error && <ApiErrorPanel error={error} onRetry={load} />}
      {data && (
        <LhbSourceBanner status={data.source_status as LhbSourceStatus} asOf={data.as_of} errorReason={data.error_reason} />
      )}
      {data && (data.nodes?.length || 0) > 0 ? (
        <div className="mb-5 grid gap-2 sm:grid-cols-2">
          {data.nodes?.map((node) => (
            <div key={node.actor_id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
              <div className="font-medium">{node.label}</div>
              <div className="mt-1 text-xs text-slate-500">
                {node.stock_count} 只股票 · 净额 {formatYuan(node.net_yuan)}
              </div>
              <div className="mt-1 font-mono text-[11px] text-slate-400">{node.actor_id}</div>
            </div>
          ))}
        </div>
      ) : null}
      {data && data.items.length === 0 ? (
        <EmptyState title="无独立主体共现边" hint="单一主体或当日无完整事件；节点仍可单独展示" />
      ) : data ? (
        <table className="w-full rounded-lg border border-slate-200 bg-white text-sm">
          <thead>
            <tr className="border-b text-left text-slate-400">
              <th className="px-3 py-2">主体 A</th>
              <th className="px-3 py-2">主体 B</th>
              <th className="px-3 py-2">共现次数</th>
              <th className="px-3 py-2">股票</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((edge) => (
              <tr key={`${edge.source_actor_id}:${edge.target_actor_id}`} className="border-b border-slate-50">
                <td className="px-3 py-2 font-mono text-xs">{edge.source_actor_id}</td>
                <td className="px-3 py-2 font-mono text-xs">{edge.target_actor_id}</td>
                <td className="px-3 py-2">{edge.weight}</td>
                <td className="px-3 py-2 font-mono text-xs">{edge.ts_codes.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}
