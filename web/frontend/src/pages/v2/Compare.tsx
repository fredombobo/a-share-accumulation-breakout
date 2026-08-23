/**
 * v2 对比页：2-6 只标的 K 线/最近行情对比（只读）。
 */
import { useEffect, useState } from 'react'
import { api, type KlinePoint } from '../../api/client'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'

const MIN_CODES = 2
const MAX_CODES = 6

function parseCodes(raw: string): string[] {
  return raw
    .split(/[\s,，;；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export default function Compare() {
  const [input, setInput] = useState('')
  const [codes, setCodes] = useState<string[]>([])
  const [series, setSeries] = useState<Record<string, KlinePoint[]>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (codes.length < MIN_CODES) return
    let alive = true
    setLoading(true)
    setError('')
    Promise.all(codes.map((c) => api.kline(c).then((r) => [c, r.kline] as const)))
      .then((rows) => {
        if (!alive) return
        setSeries(Object.fromEntries(rows))
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [codes])

  const submit = () => {
    const next = parseCodes(input)
    if (next.length < MIN_CODES) {
      setError(`至少输入 ${MIN_CODES} 个标的`)
      return
    }
    if (next.length > MAX_CODES) {
      setError(`最多 ${MAX_CODES} 个标的`)
      return
    }
    setCodes(next)
  }

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">对比</h1>
      <p className="text-sm text-slate-500 mb-6">2–6 只标的 K 线对比 · 只读</p>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder={`输入 ${MIN_CODES}-${MAX_CODES} 个代码，如 000001.SZ 600000.SH`}
          aria-label="对比标的"
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          onClick={submit}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          对比
        </button>
      </div>

      {error && <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: true, status: 0 }} />}
      {loading && <div className="mt-4 text-slate-500">加载 K 线…</div>}

      {Object.keys(series).length > 0 && (
        <div className="mt-4 space-y-3">
          {Object.entries(series).map(([code, points]) => {
            const last = points[points.length - 1]
            const first = points[0]
            const pct = first && last ? ((last.close - first.close) / first.close) * 100 : null
            return (
              <div key={code} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-center justify-between">
                  <span className="font-mono font-semibold">{code}</span>
                  <span className="text-xs text-slate-400">{points.length} 根 K 线</span>
                </div>
                {last && (
                  <div className="mt-2 flex items-center gap-4 text-sm">
                    <span className="font-mono">{last.close}</span>
                    {pct != null && (
                      <span className={pct >= 0 ? 'text-red-600' : 'text-emerald-600'}>
                        {pct >= 0 ? '+' : ''}
                        {pct.toFixed(2)}%
                      </span>
                    )}
                    <span className="text-xs text-slate-400">{last.trade_date}</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
