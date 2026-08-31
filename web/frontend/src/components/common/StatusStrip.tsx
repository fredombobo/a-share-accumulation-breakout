export type StatusTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

const TONE_CLASS: Record<StatusTone, string> = {
  ok: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  warn: 'bg-amber-100 text-amber-800 border-amber-200',
  danger: 'bg-red-100 text-red-800 border-red-200',
  info: 'bg-sky-100 text-sky-800 border-sky-200',
  neutral: 'bg-slate-100 text-slate-700 border-slate-200',
}

const DOT_CLASS: Record<StatusTone, string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-500',
  danger: 'bg-red-500',
  info: 'bg-sky-500',
  neutral: 'bg-slate-400',
}

export function StatusStrip({
  tone = 'neutral',
  label,
}: {
  tone?: StatusTone
  label: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE_CLASS[tone]}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_CLASS[tone]}`} />
      {label}
    </span>
  )
}

/** 从布尔/字符串状态推导 tone。 */
export function toneFromStatus(status: string | boolean | undefined | null): StatusTone {
  if (status == null || status === '') return 'neutral'
  if (typeof status === 'boolean') return status ? 'ok' : 'danger'
  const s = String(status).toLowerCase()
  if (['ok', 'pass', 'ready', 'done', 'complete', 'succeeded', 'active', 'healthy', 'fresh', 'true', '1'].includes(s)) return 'ok'
  if (['fail', 'failed', 'blocked', 'error', 'stale', 'invalid', 'false', '0', 'cancelled', 'expired'].includes(s)) return 'danger'
  if (['warn', 'warning', 'degraded', 'pending', 'running', 'queued', 'cancelling', 'insufficient'].includes(s)) return 'warn'
  if (s === '') return 'neutral'
  return 'info'
}
