import { useEffect, useState } from 'react'

export interface CorrectiveReplaySummary {
  schema: 'ab-corrective-replay-v1'
  completed_at: string
  original_parameters_unchanged: boolean
  original_rows_unchanged: boolean
  models: { execution: string; portfolio: string; code: string }
  arms: {
    label: string
    original_run_id: string
    run_id: string
    before: { return: number | null; drawdown: number | null; trades: number | null }
    after: { return: number | null; drawdown: number | null; trades: number | null }
    accounting_passed: boolean
    verdict: string
  }[]
}

const pct = (value: number | null) => value == null ? '未取得' : `${(value * 100).toFixed(3)}%`

export default function ReplayCorrectionPanel() {
  const [summary, setSummary] = useState<CorrectiveReplaySummary | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/reliability/replay-summary.json', { signal: controller.signal, cache: 'no-store' })
      .then(async response => { if (!response.ok) throw new Error('unavailable'); return response.json() as Promise<CorrectiveReplaySummary> })
      .then(data => { if (data.schema !== 'ab-corrective-replay-v1' || !Array.isArray(data.arms)) throw new Error('unsupported'); setSummary(data) })
      .catch(() => { if (!controller.signal.aborted) setUnavailable(true) })
    return () => controller.abort()
  }, [])

  return <section className="replay-correction" aria-label="原参数纠错重算">
    <div className="replay-correction-heading"><div><span className="guide-eyebrow">EXECUTION REVIEW</span><h2>原参数纠错重算</h2><p>核对成交可达性、资金时序与账户守恒，保留原始研究作对照。</p></div><span className="pill">{summary ? `${summary.arms.length} 组归档摘要` : unavailable ? '记录待载入' : '正在读取'}</span></div>
    {summary ? <>
      <div className="replay-model-meta"><span>成交模型 <b>{summary.models.execution}</b></span><span>组合模型 <b>{summary.models.portfolio}</b></span><span>{summary.original_parameters_unchanged && summary.original_rows_unchanged ? '原参数不变 · 原报告保留' : '原始身份核验未通过'}</span></div>
      <div className="replay-table-scroll"><table><thead><tr><th>研究组</th><th>原 OOS 收益</th><th>纠错后收益</th><th>纠错后回撤</th><th>完成交易</th><th>账户核对</th><th>研究结论</th></tr></thead><tbody>{summary.arms.map(arm => <tr key={arm.run_id}><th scope="row" title={`${arm.original_run_id} → ${arm.run_id}`}>{arm.label}</th><td className="num">{pct(arm.before.return)}</td><td className="num">{pct(arm.after.return)}</td><td className="num">{pct(arm.after.drawdown)}</td><td className="num">{arm.before.trades ?? '—'} → {arm.after.trades ?? '—'}</td><td>{arm.accounting_passed ? '通过' : '待核对'}</td><td>{arm.verdict}</td></tr>)}</tbody></table></div>
      <p className="replay-boundary">表中结果与账户核对状态来自归档摘要。2026-09-19 复核尚未找到对应的纠错原始报告，逐笔复算和最终证据验收仍待补齐。这四组使用已经观察过的历史数据，不能算作新的样本外验证，也不会自动启用参数。下方历史报告仍保留生成时的模型和结论。</p>
      <a className="overview-text-button" href="/reliability/replay-summary.json" download>下载纠错对照与版本记录 ↓</a>
    </> : <p className="replay-boundary">纠错记录尚未载入。旧报告的成交假设应与生成版本一起解读。</p>}
  </section>
}
