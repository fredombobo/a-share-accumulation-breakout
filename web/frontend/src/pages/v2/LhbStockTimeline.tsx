import { useState } from 'react'
import { fetchLhbTimeline, formatYuan } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbEvent, LhbSourceStatus } from '../../types/lhb'

export default function LhbStockTimeline() {
  const [tsCode, setTsCode] = useState('000001.SZ')
  const [data, setData] = useState<LhbEnvelope<LhbEvent> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      setData(await fetchLhbTimeline(tsCode))
      return null
    })

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-semibold mb-1">股票龙虎榜时间线</h1>
      <p className="text-sm text-slate-500 mb-4">按披露日排列 · 金额为元</p>
      <div className="mb-4 flex gap-2">
        <input
          value={tsCode}
          onChange={(e) => setTsCode(e.target.value)}
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
      {data && data.items.length === 0 ? (
        <EmptyState title="无上榜记录" />
      ) : data ? (
        <ol className="space-y-2">
          {data.items.map((ev) => (
            <li key={ev.event_id} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
              <span className="font-mono text-xs mr-2">{ev.disclose_date}</span>
              {ev.reason_raw}
              <span className="ml-2 text-slate-500">{formatYuan(ev.payload?.net_yuan ?? ev.payload?.buy_yuan)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  )
}
