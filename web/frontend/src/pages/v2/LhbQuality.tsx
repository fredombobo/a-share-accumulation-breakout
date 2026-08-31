import { useState } from 'react'
import { defaultTradeDate, fetchLhbQuality } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbSourceStatus } from '../../types/lhb'

export default function LhbQuality() {
  const [tradeDate, setTradeDate] = useState(defaultTradeDate)
  const [data, setData] = useState<LhbEnvelope<Record<string, unknown>> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      setData(await fetchLhbQuality(tradeDate))
      return null
    })

  const row = data?.items[0]

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">数据质量与缺口</h1>
      <p className="text-sm text-slate-500 mb-4">VALID_EMPTY / NOT_PUBLISHED / FETCH_FAILED 分开显示</p>
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
      {!row ? (
        data ? <EmptyState title="无质量摘要" /> : null
      ) : (
        <pre className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs overflow-auto">
          {JSON.stringify(row, null, 2)}
        </pre>
      )}
    </div>
  )
}
