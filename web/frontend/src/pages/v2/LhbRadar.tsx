import { useState } from 'react'
import { defaultTradeDate, fetchLhbRadar, formatYuan } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbEvent, LhbSourceStatus } from '../../types/lhb'

export default function LhbRadar() {
  const [tradeDate, setTradeDate] = useState(defaultTradeDate)
  const [data, setData] = useState<LhbEnvelope<LhbEvent> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      const body = await fetchLhbRadar(tradeDate)
      setData(body)
      return null
    })

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-semibold mb-1">每日龙虎榜雷达</h1>
      <p className="text-sm text-slate-500 mb-4">研究观察 · 下一开盘模型 · 非跟单</p>
      <div className="mb-4 flex gap-2">
        <input
          value={tradeDate}
          onChange={(e) => setTradeDate(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 font-mono text-sm"
          placeholder="YYYYMMDD"
        />
        <button type="button" onClick={load} className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white">
          查询
        </button>
      </div>
      {error && <ApiErrorPanel error={error} onRetry={load} />}
      {data && (
        <LhbSourceBanner
          status={data.source_status as LhbSourceStatus}
          asOf={data.as_of}
          errorReason={data.error_reason}
        />
      )}
      {data && data.items.length === 0 ? (
        <EmptyState
          title={
            data.source_status === 'NOT_PUBLISHED'
              ? '尚未发布'
              : data.source_status === 'FETCH_FAILED'
                ? '抓取失败'
                : '当日无龙虎榜'
          }
          hint="空数组不等于失败；请看上方 source_status"
        />
      ) : data ? (
        <table className="w-full text-sm border border-slate-200 rounded-lg bg-white">
          <thead>
            <tr className="text-left text-slate-400 border-b border-slate-100">
              <th className="px-3 py-2">代码</th>
              <th className="px-3 py-2">原因</th>
              <th className="px-3 py-2">窗口</th>
              <th className="px-3 py-2">买入（元）</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((ev) => (
              <tr key={ev.event_id} className="border-b border-slate-50">
                <td className="px-3 py-2 font-mono text-xs">{ev.ts_code}</td>
                <td className="px-3 py-2">{ev.reason_raw}</td>
                <td className="px-3 py-2 font-mono text-xs">{ev.window_code}</td>
                <td className="px-3 py-2 font-mono text-xs">{formatYuan(ev.payload?.buy_yuan)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}
