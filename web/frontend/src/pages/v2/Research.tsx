/**
 * v2 研究治理页面（P7.3）：实验登记列表 + 新建实验（受控写，幂等）。
 */
import { useState } from 'react'
import { fetchExperiments, registerExperiment } from '../../api/research'
import type { Experiment } from '../../types/research'
import { ApiErrorPanel } from '../../components/common/ApiErrorPanel'
import { EmptyState } from '../../components/common/EmptyState'
import { useApiError } from '../../hooks/useApiError'

const STATUS_STYLE: Record<string, string> = {
  REGISTERED: 'bg-slate-100 text-slate-600',
  RUNNING: 'bg-sky-50 text-sky-700',
  COMPLETED: 'bg-emerald-50 text-emerald-700',
  CANCELLED: 'bg-slate-100 text-slate-400',
  REJECTED: 'bg-rose-50 text-rose-700',
}

export default function Research() {
  const [items, setItems] = useState<Experiment[]>([])
  const [loaded, setLoaded] = useState(false)
  const [strategy, setStrategy] = useState('accumulation_breakout_v1')
  const [configHash, setConfigHash] = useState('cfg-dev')
  const { error, run } = useApiError()

  const load = () =>
    run(async () => {
      const data = await fetchExperiments()
      setItems(data.items || [])
      setLoaded(true)
      return null
    })

  const create = () =>
    run(async () => {
      await registerExperiment({ strategy, params: { vol_ratio: 1.6 }, config_hash: configHash })
      await load()
      return null
    })

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-semibold mb-1">研究治理</h1>
      <p className="text-sm text-slate-500 mb-6">实验登记 · trial ledger · 注册后核心字段不可变</p>
      {error && <ApiErrorPanel error={error} />}

      <div className="rounded-xl border border-slate-200 bg-white p-4 mb-6">
        <h2 className="text-sm font-medium text-slate-500 mb-3">登记新实验（幂等）</h2>
        <div className="flex flex-wrap gap-2">
          <input
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono"
            placeholder="strategy"
          />
          <input
            value={configHash}
            onChange={(e) => setConfigHash(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono"
            placeholder="config_hash"
          />
          <button
            onClick={create}
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            登记
          </button>
          <button
            onClick={load}
            className="rounded-lg bg-slate-100 px-4 py-1.5 text-sm text-slate-700 hover:bg-slate-200"
          >
            刷新
          </button>
        </div>
      </div>

      {loaded && items.length === 0 ? (
        <EmptyState title="暂无实验登记" hint="登记第一个实验开始治理" />
      ) : (
        <table className="w-full text-sm border border-slate-200 rounded-lg bg-white">
          <thead>
            <tr className="text-left text-slate-400 border-b border-slate-100">
              <th className="px-3 py-2">实验 ID</th>
              <th className="px-3 py-2">策略</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">config_hash</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.experiment_id} className="border-b border-slate-50 hover:bg-slate-50">
                <td className="px-3 py-2 font-mono text-xs">{e.experiment_id}</td>
                <td className="px-3 py-2 font-mono text-xs">{e.strategy}</td>
                <td className="px-3 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[e.status] || 'bg-slate-100'}`}>
                    {e.status}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{e.config_hash}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
