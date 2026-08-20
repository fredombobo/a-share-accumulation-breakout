import type { LabReportHistoryItem, LabTrustedReport } from '../api/client'

const number = (value: unknown, digits = 3) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '—'
}

const percent = (value: unknown) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

export default function LabTrustedReportView({
  report,
  history,
  onSelectHistory,
}: {
  report: LabTrustedReport
  history: LabReportHistoryItem[]
  onSelectHistory: (runId: string) => void
}) {
  const verdictClass = report.verdict === 'PASS' ? 'pass' : report.verdict === 'FAIL' ? 'fail' : 'insufficient'
  const downloadBase = `/api/lab/reports/${encodeURIComponent(report.research_run_id)}/download`
  return (
    <section className={`card lab-trusted-report ${verdictClass}`}>
      <div className="lab-report-verdict">
        <div>
          <div className="lab-kicker">TRUSTED RESEARCH REPORT</div>
          <h2>可信研究结论：{report.verdict}</h2>
          <p>{report.summary}</p>
        </div>
        <div className="lab-report-actions">
          <a className="btn" href={`${downloadBase}?format=markdown`}>下载 Markdown</a>
          <a className="btn" href={`${downloadBase}?format=json`}>下载 JSON</a>
        </div>
      </div>

      {report.block_reasons?.length > 0 && (
        <div className="lab-report-blockers">
          <strong>人话阻断原因</strong>
          <ul>{report.block_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      )}

      <div className="lab-report-grid">
        <article>
          <h3>样本与版本</h3>
          <dl>
            <div><dt>股票池</dt><dd>{report.sample?.universe_size ?? '—'} 只</dd></div>
            <div><dt>数据</dt><dd className="mono">{report.versions?.dataset || '—'}</dd></div>
            <div><dt>代码</dt><dd className="mono">{report.versions?.code || '—'}</dd></div>
            <div><dt>成本</dt><dd className="mono">{report.versions?.cost || '—'}</dd></div>
          </dl>
        </article>
        <article>
          <h3>冻结的 IS 第一名 / OOS</h3>
          <dl>
            <div><dt>参数 ID</dt><dd className="mono">{report.primary_is?.param_id || '—'}</dd></div>
            <div><dt>IS 净 PF</dt><dd>{number(report.primary_is?.net_profit_factor)}</dd></div>
            <div><dt>OOS 净 PF</dt><dd>{number(report.primary_oos?.oos_net_profit_factor)}</dd></div>
            <div><dt>OOS 净回撤</dt><dd>{percent(report.primary_oos?.oos_net_max_drawdown)}</dd></div>
          </dl>
        </article>
        <article>
          <h3>固定成本口径</h3>
          <p className="mono lab-report-code">{JSON.stringify(report.cost_assumptions)}</p>
        </article>
      </div>

      <div className="lab-report-split">
        <div>
          <h3>Walk-forward（三窗均需完整）</h3>
          <div className="lab-table-wrap">
            <table className="lab-table">
              <thead><tr><th>窗口</th><th className="num">训练净PF</th><th className="num">测试净PF</th><th className="num">交易</th><th className="num">回撤</th></tr></thead>
              <tbody>
                {report.wf_windows?.map((row) => (
                  <tr key={row.window}>
                    <td>{row.window}</td><td className="num">{number(row.train_pf)}</td>
                    <td className="num">{number(row.test_pf)}</td><td className="num">{row.test_n ?? '—'}</td>
                    <td className="num">{percent(row.test_dd)}</td>
                  </tr>
                ))}
                {!report.wf_windows?.length && <tr><td colSpan={5}>无完整 WF 证据</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3>净收益基线对照</h3>
          <div className="lab-table-wrap">
            <table className="lab-table">
              <thead><tr><th>基线</th><th className="num">交易</th><th className="num">净均收益</th><th className="num">净PF</th><th className="num">净回撤</th></tr></thead>
              <tbody>
                {(['random', 'ma20_60'] as const).map((key) => {
                  const row = report.baselines?.[key]
                  return <tr key={key}><td>{key === 'random' ? '随机（种子 20260808）' : 'MA20/60'}</td><td className="num">{row?.n_trades ?? '—'}</td><td className="num">{percent(row?.net_avg_return)}</td><td className="num">{number(row?.net_profit_factor)}</td><td className="num">{percent(row?.net_max_drawdown)}</td></tr>
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="lab-report-checks">
        <h3>门禁检查</h3>
        {report.checks?.map((check) => (
          <div key={check.id} className={check.passed ? 'ok' : 'bad'}>
            <span>{check.passed ? '✓' : '×'}</span>
            <strong>{check.label}</strong>
            <small>实际 {JSON.stringify(check.actual)} · 要求 {check.threshold}</small>
          </div>
        ))}
      </div>

      <div className="lab-report-footer">
        <div>
          <strong>IS 第二/三名只作敏感性</strong>
          <span>{report.sensitivity?.map((row) => row.param_id).filter(Boolean).join(' / ') || '—'}</span>
        </div>
        <div>
          <strong>历史报告</strong>
          <span className="lab-report-history">
            {history.slice(0, 6).map((item) => (
              <button type="button" key={item.research_run_id} onClick={() => onSelectHistory(item.research_run_id)}>
                {item.verdict || '—'} · {item.finished_at?.slice(0, 10) || item.research_run_id}
              </button>
            ))}
          </span>
        </div>
      </div>
      <p className="note">即使 PASS，也只登记为隔离候选参数，不会自动进入 A 池或生成订单。</p>
    </section>
  )
}
