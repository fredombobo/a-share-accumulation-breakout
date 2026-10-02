import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { forwardApi, type ForwardCapture, type ForwardOutcome, type ForwardStatus } from '../api/forward'
import './ForwardObservations.css'

const date = (value?: string | null) => value && /^\d{8}$/.test(value) ? `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6)}` : '—'
const percent = (value: number | null) => value == null ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
const statusLabels: Record<string, string> = {
  WAITING_HORIZON: '尚未到期', CALENDAR_UNVERIFIED: '观察日待核对', BASE_QUOTE_MISSING: '基准行情缺失',
  TARGET_QUOTE_MISSING: '到期行情缺失', RAW_ONLY: '复权因子待补齐', OBSERVED: '已取得观察',
}

export default function ForwardObservations() {
  const nav = useNavigate()
  const [status, setStatus] = useState<ForwardStatus | null>(null)
  const [history, setHistory] = useState<ForwardCapture[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [runId, setRunId] = useState('')
  const [horizon, setHorizon] = useState(5)
  const [offset, setOffset] = useState(0)
  const [allRevisions, setAllRevisions] = useState(false)
  const [outcomes, setOutcomes] = useState<ForwardOutcome[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [revision, setRevision] = useState(0)
  const historyRequest = useRef(0)
  const selectedRun = useRef('')
  const selected = history.find(item => item.run_id === runId)

  const load = useCallback(() => {
    const requestId = ++historyRequest.current
    setError('')
    return Promise.all([forwardApi.status(), forwardApi.history(historyOffset)])
      .then(([next, runs]) => {
        if (requestId !== historyRequest.current) return
        setStatus(next); setHistory(runs.items); setHistoryTotal(runs.total)
        const nextRun = runs.items.some(item => item.run_id === selectedRun.current) ? selectedRun.current : runs.items[0]?.run_id || ''
        if (nextRun !== selectedRun.current) {
          selectedRun.current = nextRun; setRunId(nextRun); setOffset(0)
        }
      }).catch(reason => { if (requestId === historyRequest.current) setError(reason instanceof Error ? reason.message : String(reason)) })
  }, [historyOffset])
  useEffect(() => { void load(); return () => { historyRequest.current += 1 } }, [load])

  useEffect(() => {
    const controller = new AbortController()
    setOutcomes([]); setTotal(0)
    if (!runId) { setLoading(false); return }
    setLoading(true)
    forwardApi.results(runId, horizon, offset, allRevisions, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { setOutcomes(result.items); setTotal(result.total) } })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [runId, horizon, offset, allRevisions, revision])

  const runAction = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(''); setFeedback('')
    try {
      const response = await action() as { status?: string; message?: string }
      if (response.status === 'FAILED' || response.status === 'REJECTED') throw new Error(response.message || '操作未完成')
      setFeedback(success); await load(); setRevision(value => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  const download = () => {
    const blob = new Blob([JSON.stringify({ capture: selected, horizon, total, offset, all_revisions: allRevisions, items: outcomes, note: status?.note }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob); const link = document.createElement('a')
    link.href = url; link.download = `ab-forward-${runId}-${horizon}d-page${offset / 100 + 1}.json`; link.click(); URL.revokeObjectURL(url)
  }

  return <div className="forward-workspace fade-up">
    <header className="forward-heading"><div><span className="forward-eyebrow">PROSPECTIVE OBSERVATIONS</span><h1>前瞻观察</h1><p>先冻结名单，再观察未来。为每天的选股积累可以复核的记录。</p></div><button className="btn" type="button" onClick={() => nav('/')}>返回每日选股 ↗</button></header>
    <div className="forward-protocol"><span className={`forward-dot ${status?.enabled ? 'on' : ''}`} /><b>{status?.enabled ? '记录已启用' : status ? '尚未启用' : '正在核对协议'}</b><span>{status?.protocol ? `开始于 ${status.protocol.enabled_at.slice(0, 19).replace('T', ' ')}` : '只接收启用后的新扫描'}</span>{status?.protocol && <code>{status.protocol.version}</code>}</div>
    {error && <div className="forward-message is-error" role="alert"><span>{error}</span><button className="forward-link" onClick={() => { void load(); setRevision(value => value + 1) }}>重新读取</button></div>}
    {feedback && <div className="forward-message" role="status">{feedback}</div>}
    {status && !status.enabled ? <section className="forward-start"><span className="forward-eyebrow">START A RECORD</span><h2>从下一次扫描开始积累证据</h2><p>保存完整的 A 池、B 池和数据不足名单。空结果也会留下记录；旧扫描不能事后改记为前瞻样本。</p><button className="btn btn-primary" disabled={busy} onClick={() => void runAction(forwardApi.enable, '观察记录已启用。请运行一次新的每日扫描。')}>{busy ? '正在启用…' : '启用前瞻记录'}</button></section> : <>
      <section className="forward-metrics" aria-label="前瞻记录概况">{[['扫描记录', status?.counts.captures], ['主样本', status?.counts.primary], ['重复观察', status?.counts.secondary], ['冻结候选', status?.counts.candidates]].map(([label, value]) => <div key={label}><span>{label}</span><b className="num">{value ?? '—'}</b></div>)}</section>
      {status?.missing_primary_runs?.length ? <div className="forward-message is-error">有 {status.missing_primary_runs.length} 个预定主样本尚未成功记录。其后的重复观察不会替代它们。</div> : null}
      {status?.uncaptured_runs?.length ? <div className="forward-message is-error"><span>有 {status.uncaptured_runs.length} 次新扫描尚未记录。可在有效期限内重试，超期会明确拒绝。</span><button className="forward-link" disabled={busy} onClick={() => void runAction(() => forwardApi.capture(status.uncaptured_runs![0]), '已处理记录请求，请核对主样本状态。')}>重试首个待记录扫描</button></div> : null}
      {status?.last_error && <details className="forward-last-error"><summary>最近一次未完成记录</summary><p>{status.last_error.occurred_at.slice(0, 19).replace('T', ' ')} · {status.last_error.message || status.last_error.reason}</p></details>}
      <div className="forward-observation-heading"><div><span className="forward-eyebrow">FROZEN COHORT</span><h2>观察记录</h2></div><button className="btn" disabled={busy || !history.length} onClick={() => void runAction(() => forwardApi.refresh(), '已核对到期观察。新增结果另存版本，原记录保留。')}>{busy ? '正在核对…' : '更新到期观察'}</button></div>
      {!history.length ? <section className="forward-empty"><span>01</span><h3>{status?.enabled ? '等待下一次成功扫描' : '等待记录状态'}</h3><p>启用之后，在下一交易日集合竞价开始前完成新扫描，系统才会保存前瞻名单。暂时没有样本时，不展示推算收益。</p>{status?.enabled && <button className="forward-link" onClick={() => nav('/')}>前往每日选股 →</button>}</section> : <>
        <div className="forward-controls"><label>冻结名单<select value={runId} onChange={event => { selectedRun.current = event.target.value; setRunId(event.target.value); setOffset(0) }}>{history.map(item => <option key={item.run_id} value={item.run_id}>{date(item.as_of)} · {item.role === 'PRIMARY' ? '主样本' : '重复观察'} · {item.counts.total} 只 · {item.run_id}</option>)}</select></label><div className="forward-horizons" role="tablist" aria-label="观察期限">{[1, 5, 10, 20].map(value => <button role="tab" aria-selected={horizon === value} className={horizon === value ? 'active' : ''} onClick={() => { setHorizon(value); setOffset(0) }} key={value}>{value} 日</button>)}</div></div>
        {historyTotal > 50 && <div className="forward-pager"><button disabled={!historyOffset} onClick={() => setHistoryOffset(value => Math.max(0, value - 50))}>较新记录</button><span>{historyOffset + 1}–{Math.min(historyOffset + 50, historyTotal)} / {historyTotal}</span><button disabled={historyOffset + 50 >= historyTotal} onClick={() => setHistoryOffset(value => value + 50)}>较早记录</button></div>}
        {selected && <div className="forward-cohort-meta"><span>{selected.role === 'PRIMARY' ? '预定主样本' : '重复观察 · 不替代主样本'}</span><span>A {selected.counts.A} / B {selected.counts.B} / 数据不足 {selected.counts.DATA_INCOMPLETE}</span><span>条件 <code title={selected.entry_hash}>{selected.entry_hash.slice(0, 12)}</code></span><button className="forward-link" onClick={() => nav(`/?run_id=${encodeURIComponent(selected.run_id)}`)}>查看当次名单 ↗</button></div>}
        <div className="forward-result-tools"><label><input type="checkbox" checked={allRevisions} onChange={event => { setAllRevisions(event.target.checked); setOffset(0) }} />显示历次修订</label><button className="forward-link" disabled={!outcomes.length} onClick={download}>下载本页证据 ↓</button></div>
        {loading ? <div className="forward-empty" role="status">正在读取观察…</div> : outcomes.length ? <div className="forward-table-scroll"><table><thead><tr><th>股票</th><th>分组</th><th>目标交易日</th><th>原价变化</th><th>复权变化</th><th>状态 / 版本</th></tr></thead><tbody>{outcomes.map(item => <tr key={`${item.run_id}-${item.ts_code}-${item.horizon}-${item.revision}`}><th scope="row"><button onClick={() => nav(`/stock/${item.ts_code}?run_id=${encodeURIComponent(item.run_id)}`)}>{item.ts_code}</button></th><td>{item.group === 'DATA_INCOMPLETE' ? '数据不足' : `${item.group} 池`}</td><td className="num">{date(item.target_date)}</td><td className="num">{percent(item.raw_return)}</td><td className="num">{percent(item.adjusted_return)}</td><td><span className={item.status === 'OBSERVED' ? 'observed' : 'pending'}>{statusLabels[item.status] || item.status}</span><small title={item.input_hash}>v{item.revision} · {item.computed_at.slice(0, 16).replace('T', ' ')}</small>{item.restated?.differs_from_capture && <details className="forward-restatement"><summary>基准数据有后补或修订</summary><p>主列保留捕获时的基准。按当前修订数据计算：原价 {percent(item.restated.raw_return)}，复权 {percent(item.restated.adjusted_return)}。</p><small>此口径不替代原始观察，可下载本页核对修订依据。</small></details>}</td></tr>)}</tbody></table></div> : <div className="forward-empty"><h3>{selected?.counts.total === 0 ? '这次扫描为零候选，记录已保留' : '尚未计算这段观察'}</h3><p>{selected?.counts.total === 0 ? '零结果参与扫描记录统计，没有个股收益可以计算。' : '点击“更新到期观察”，系统会核对日期和行情；未到期仍显示待观察。'}</p></div>}
        {total > 100 && <div className="forward-pager"><button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 100))}>上一页</button><span>{offset + 1}–{Math.min(offset + 100, total)} / {total}</span><button disabled={offset + 100 >= total} onClick={() => setOffset(value => value + 100)}>下一页</button></div>}
      </>}
    </>}
    <section className="forward-method"><h3>这些数字如何解读</h3><div><p><b>观察口径</b>{status?.note || '扫描日收盘至后续交易日收盘的价格变化，不是账户或策略的成交收益。'}</p><p><b>避免事后挑选</b>同日相同入场条件的首次合格发布预定为主样本。更改展示数量或退出参数，不会重置主样本。</p><p><b>保留未知</b>缺行情、停牌或缺复权因子会明确标记。因子不齐时仅显示原价变化，不把它解释成可实现收益。</p></div></section>
  </div>
}
