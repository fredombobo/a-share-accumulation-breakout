import { StatusStrip, type StatusTone } from '../common/StatusStrip'
import type { LhbSourceStatus } from '../../types/lhb'

const TONE: Record<LhbSourceStatus, StatusTone> = {
  COMPLETE: 'ok',
  DEGRADED: 'warn',
  VALID_EMPTY: 'info',
  NOT_PUBLISHED: 'warn',
  FETCH_FAILED: 'danger',
}

const LABEL: Record<LhbSourceStatus, string> = {
  COMPLETE: '数据完整',
  DEGRADED: '降级源，不可标 confirmed',
  VALID_EMPTY: '已发布但当日无榜',
  NOT_PUBLISHED: '尚未发布',
  FETCH_FAILED: '抓取失败',
}

export function LhbSourceBanner({
  status,
  asOf,
  errorReason,
}: {
  status: LhbSourceStatus
  asOf?: string
  errorReason?: string | null
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
      <StatusStrip tone={TONE[status]} label={LABEL[status]} />
      <span className="text-xs text-slate-500">金额单位：元（非万元）</span>
      {asOf && <span className="font-mono text-xs text-slate-400">as_of {asOf}</span>}
      {errorReason && <span className="text-xs text-red-600">{errorReason}</span>}
      <span className="text-xs text-slate-400">研究 overlay · 不产生订单</span>
    </div>
  )
}
