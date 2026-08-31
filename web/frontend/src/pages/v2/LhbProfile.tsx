import { useState } from 'react'
import { fetchLhbSeat, formatYuan } from '../../api/lhb'
import { LhbSourceBanner } from '../../components/lhb/LhbSourceBanner'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { StatusStrip } from '../../components/common/StatusStrip'
import { useApiError } from '../../hooks/useApiError'
import type { LhbEnvelope, LhbProfile, LhbSourceStatus } from '../../types/lhb'

export default function LhbProfilePage() {
  const [seatId, setSeatId] = useState('')
  const [asOf, setAsOf] = useState(() => new Date().toISOString())
  const [data, setData] = useState<LhbEnvelope<LhbProfile> | null>(null)
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      setData(await fetchLhbSeat(seatId, asOf))
      return null
    })

  const profile = data?.items[0]
  const lowConf = profile?.identity?.evidence_grade === 'C'

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">席位 / 身份画像</h1>
      <p className="text-sm text-slate-500 mb-4">小样本已收缩 · 身份仅为假设 · 禁止实名断言</p>
      <div className="mb-4 flex flex-wrap gap-2">
        <input
          value={seatId}
          onChange={(e) => setSeatId(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 font-mono text-sm"
          placeholder="seat_id"
        />
        <input
          value={asOf}
          onChange={(e) => setAsOf(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 font-mono text-sm w-72"
        />
        <button type="button" onClick={load} disabled={!seatId.trim()} className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-40">
          查询
        </button>
      </div>
      {error && <ApiErrorPanel error={error} onRetry={load} />}
      {data && (
        <LhbSourceBanner status={data.source_status as LhbSourceStatus} asOf={data.as_of} errorReason={data.error_reason} />
      )}
      {!profile ? (
        data ? <EmptyState title="无画像快照" hint="样本不足或尚未生成" /> : null
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
          <div className="text-sm text-slate-500">
            窗口 {profile.window_days} 日 · 样本 {profile.sample_size} · 最近 {profile.last_event_date || '—'}
          </div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div>买入 {formatYuan(profile.buy_yuan)}</div>
            <div>卖出 {formatYuan(profile.sell_yuan)}</div>
            <div>净额 {formatYuan(profile.net_yuan)}</div>
          </div>
          <div className="text-sm">
            展示胜率 {profile.display_win_rate == null ? '—' : `${(profile.display_win_rate * 100).toFixed(1)}%`}
            {profile.reliable_100pct_forbidden ? (
              <span className="ml-2 text-amber-700">小样本禁止展示 100% 可靠胜率</span>
            ) : null}
          </div>
          {profile.identity ? (
            <div className="text-sm">
              <StatusStrip tone={lowConf ? 'warn' : 'info'} label={`证据 ${profile.identity.evidence_grade}`} />
              <span className="ml-2">{profile.identity.identity_language}</span>
              <span className="ml-2 font-mono text-xs">置信 {profile.identity.confidence}</span>
            </div>
          ) : (
            <div className="text-xs text-slate-400">无身份假设</div>
          )}
        </div>
      )}
    </div>
  )
}
