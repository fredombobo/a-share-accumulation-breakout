/**
 * v2 市场情报页面（P7.3）：搜索 / 档案 / 市场宽度 / 数据状态（只读）。
 */
import { useEffect, useState } from 'react'
import {
  fetchBreadth,
  fetchDataStatus,
  fetchDeskSupplement,
  searchStocks,
} from '../../api/intelligence'
import type {
  Breadth,
  DataSourceStatus,
  DeskSupplement,
  StockSearchHit,
} from '../../types/intelligence'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'

export default function Intelligence() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<StockSearchHit[]>([])
  const [breadth, setBreadth] = useState<Breadth | null>(null)
  const [dataStatus, setDataStatus] = useState<DataSourceStatus | null>(null)
  const [supplement, setSupplement] = useState<DeskSupplement | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    Promise.all([
      fetchDataStatus().catch(() => null),
      fetchDeskSupplement().catch(() => null),
    ])
      .then(async ([ds, sup]) => {
        if (!alive) return
        setDataStatus(ds)
        setSupplement(sup)
        const asOf = sup?.trade_date
        if (asOf) {
          const b = await fetchBreadth(asOf).catch(() => sup?.breadth ?? null)
          if (alive) setBreadth(b)
        } else if (sup?.breadth) {
          setBreadth(sup.breadth)
        }
      })
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [])

  const doSearch = async () => {
    if (!q.trim()) return
    try {
      setError('')
      setHits(await searchStocks(q.trim()))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">市场情报</h1>
      <p className="text-sm text-slate-500 mb-6">档案 · 宽度 · 数据来源 · 只读</p>
      {error && <ApiErrorPanel error={{ code: 'UNKNOWN', message: error, retryable: false, status: 0 }} />}

      <div className="mb-6">
        <div className="flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            placeholder="代码 / 名称 / 行业搜索"
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            onClick={doSearch}
            className="rounded-lg bg-slate-800 px-4 py-2 text-sm text-white hover:bg-slate-700"
          >
            搜索
          </button>
        </div>
        {hits.length === 0 && q ? (
          <div className="mt-3">
            <EmptyState title="无匹配标的" />
          </div>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100 border border-slate-200 rounded-lg bg-white">
            {hits.map((h) => (
              <li key={h.ts_code} className="flex justify-between px-4 py-2 text-sm">
                <span className="font-mono">{h.ts_code}</span>
                <span>{h.name}</span>
                <span className="text-slate-400">{h.industry || '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-500 mb-3">市场宽度</h2>
          {breadth ? (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-emerald-600 font-semibold">{breadth.advances}</span> 涨</div>
              <div><span className="text-rose-600 font-semibold">{breadth.declines}</span> 跌</div>
              <div>平盘 {breadth.unchanged}</div>
              <div>总数 {breadth.total}</div>
            </div>
          ) : (
            <EmptyState title="宽度数据不可用" hint="需 trade_date 行情" />
          )}
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-500 mb-3">数据来源</h2>
          {dataStatus ? (
            <div className="text-sm space-y-1">
              <div className="font-mono">daily 最新: {dataStatus.daily_latest_trade_date || '—'}</div>
              {dataStatus.active_stock_coverage && (
                <div>
                  活跃覆盖: {dataStatus.active_stock_coverage.pct}%
                  （{dataStatus.active_stock_coverage.covered_latest}/
                  {dataStatus.active_stock_coverage.total}）
                </div>
              )}
            </div>
          ) : (
            <EmptyState title="数据状态不可用" />
          )}
        </div>
      </div>

      {supplement && (
        <div className="mt-4 text-xs text-slate-400">{supplement.disclaimer}</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-500 mb-3">涨停梯队</h2>
          {supplement?.limit_up && supplement.limit_up.status === 'PASS' ? (
            <div className="text-sm space-y-2">
              <div>
                <span className="text-red-600 font-semibold">{supplement.limit_up.limit_up}</span>
                {' '}涨停
                <span className="ml-3 text-emerald-600 font-semibold">{supplement.limit_up.limit_down}</span>
                {' '}跌停
              </div>
              <ul className="divide-y divide-slate-100">
                {supplement.limit_up.items.map((it) => (
                  <li key={it.ts_code} className="flex justify-between py-1">
                    <span className="font-mono">{it.ts_code}</span>
                    <span className="text-red-600">+{it.pct_chg}%</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <EmptyState title="涨停数据不可用" hint="需 trade_date 行情" />
          )}
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-500 mb-3">主要指数</h2>
          {supplement?.indices && supplement.indices.items.length > 0 ? (
            <ul className="divide-y divide-slate-100 text-sm">
              {supplement.indices.items.map((it) => (
                <li key={it.ts_code} className="flex justify-between py-1">
                  <span>{it.name}</span>
                  <span className="font-mono">
                    {it.close}
                    {it.pct_chg != null && (
                      <span className={it.pct_chg >= 0 ? ' text-red-600' : ' text-emerald-600'}>
                        {' '}
                        {it.pct_chg >= 0 ? '+' : ''}
                        {it.pct_chg}%
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="指数数据不可用" hint="daily 中无指数 K 线" />
          )}
        </div>
      </div>
    </div>
  )
}
