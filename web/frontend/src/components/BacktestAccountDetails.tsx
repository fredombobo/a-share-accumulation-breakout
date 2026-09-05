import { useState } from 'react'
import type { EChartsOption } from 'echarts'
import type { BacktestContribution, BacktestResult } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'

// Exact display of accounting amounts; Number is used only for chart coordinates.
export function fenMoney(value?: string): string {
  if (value == null) return '未记录'
  const amount = BigInt(value)
  const abs = amount < 0n ? -amount : amount
  return `${amount < 0n ? '-' : ''}${(abs / 100n).toLocaleString('zh-CN')}.${String(abs % 100n).padStart(2, '0')}`
}

export function materialContributions(rows: BacktestContribution[]): BacktestContribution[] {
  const magnitude = (row: BacktestContribution) => {
    const amount = BigInt(row.realized_pnl_fen)
    return amount < 0n ? -amount : amount
  }
  return [...rows].sort((a, b) => magnitude(a) === magnitude(b) ? a.name.localeCompare(b.name) : magnitude(a) > magnitude(b) ? -1 : 1).slice(0, 15)
}

export default function BacktestAccountDetails({ result }: { result: BacktestResult }) {
  const colors = useChartColors()
  const [scope, setScope] = useState<'is' | 'oos'>('oos')
  const [page, setPage] = useState(0)
  const details = result.account_details?.[scope]
  const events = details?.events || []
  const eventNames: Record<string, string> = { ENTRY_FILLED: '买入成交', EXIT_FILLED: '卖出成交', ENTRY_REJECTED: '买入未成交', EXIT_RETRY: '卖出顺延' }
  const contributionChart = (rows: BacktestContribution[]): EChartsOption => ({
    grid: { left: 90, right: 35, top: 25, bottom: 35 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'value', name: '元', axisLabel: { color: colors.subtext } },
    yAxis: { type: 'category', inverse: true, data: materialContributions(rows).map(row => row.name), axisLabel: { color: colors.subtext } },
    series: [{ type: 'bar', data: materialContributions(rows).map(row => ({ value: Number(row.realized_pnl_fen) / 100, itemStyle: { color: BigInt(row.realized_pnl_fen) >= 0n ? colors.up : colors.down } })) }],
  })
  const equity: EChartsOption = {
    grid: { left: 80, right: 30, top: 25, bottom: 60 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: details?.equity_curve.map(row => row.trade_date), axisLabel: { color: colors.subtext } },
    yAxis: { type: 'value', name: '净资产（元）', scale: true, axisLabel: { color: colors.subtext } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18 }],
    series: [{ type: 'line', showSymbol: false, smooth: false, data: details?.equity_curve.map(row => Number(row.equity_fen) / 100), lineStyle: { color: colors.accent } }],
  }
  const monthly: EChartsOption = {
    grid: { left: 60, right: 25, top: 25, bottom: 40 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: details?.monthly.map(row => row.month), axisLabel: { color: colors.subtext } },
    yAxis: { type: 'value', name: '净收益 %', axisLabel: { color: colors.subtext } },
    series: [{ type: 'bar', data: details?.monthly.map(row => ({ value: Number(row.net_return) * 100, itemStyle: { color: Number(row.net_return) >= 0 ? colors.up : colors.down } })) }],
  }
  return <section className="card section-gap" aria-label="入选参数账户明细">
    <div className="h-sec"><h2>入选参数 · 账户复盘</h2>
      <div className="seg" role="group" aria-label="复盘区间">
        {(['is', 'oos'] as const).map(key => <button type="button" key={key} className={scope === key ? 'active' : ''} aria-pressed={scope === key} onClick={() => { setScope(key); setPage(0) }}>{key === 'is' ? '样本内' : '样本外'}</button>)}
      </div>
    </div>
    {!details ? <p>这份历史结果未保存所选区间的账户明细，不补造净值曲线。新建复验后可查看；没有可回放交易的区间也不会生成虚构成交。</p> : <>
      <p>{details.note}</p>
      <div className="metric-compare">
        <div className="metric-compare-block"><h4>期初 → 期末净资产（元）</h4><p className="num">{fenMoney(details.initial_equity_fen)} → {fenMoney(details.final_equity_fen)}</p></div>
        <div className="metric-compare-block"><h4>已实现 / 未实现净损益（元）</h4><p className="num">{fenMoney(details.realized_pnl_fen)} / {fenMoney(details.unrealized_pnl_fen)}</p></div>
        <div className="metric-compare-block"><h4>会计核对</h4><p>{details.reconciliation === 'EXACT_FEN' ? '逐笔现金与月度损益核对：零分差异' : '未完成'}</p></div>
      </div>
      <div className="result-chart-grid">
        <section className="result-chart-panel" aria-label="实际净资产曲线"><header><h3>实际净资产曲线</h3></header><EChart option={equity} height={300} /></section>
        <section className="result-chart-panel" aria-label="月度净收益"><header><h3>月度净收益（含浮盈亏）</h3></header><EChart option={monthly} height={300} /></section>
        {([
          ['个股已实现损益（绝对影响前 15 项）', details.stock_contribution],
          ['当前行业已实现损益（绝对影响前 15 项）', details.industry_contribution],
          ['退出原因已实现损益', details.exit_contribution],
        ] as [string, BacktestContribution[]][]).map(([title, rows]) => <section key={title} className="result-chart-panel" aria-label={title}><header><h3>{title}</h3></header>{rows.length ? <EChart option={contributionChart(rows)} height={300} /> : <p>没有已实现成交，不计算虚假贡献。</p>}</section>)}
      </div>
      <details className="section-gap"><summary>查看逐笔现金及费用（{events.length} 条事件）</summary>
        <div className="table-wrap"><table><thead><tr><th>日期</th><th>股票</th><th>事件</th><th>股数</th><th>现金变化（元）</th><th>已实现损益（元）</th><th>逐项费用（元）</th></tr></thead><tbody>
          {events.slice(page * 30, page * 30 + 30).map((event, i) => <tr key={`${page}-${i}`}><td>{event.trade_date}</td><td>{event.ts_code}</td><td>{eventNames[event.event] || event.event}</td><td>{event.qty}</td><td>{fenMoney(event.cash_delta_fen)}</td><td>{fenMoney(event.realized_pnl_fen)}</td><td>{Object.entries(event.fee_breakdown || {}).map(([key, value]) => `${({ commission_fen: '佣金', stamp_tax_fen: '税费', other_fee_fen: '其他', slippage_fen: '滑点' } as Record<string, string>)[key] || key} ${fenMoney(value)}`).join(' / ')}</td></tr>)}
        </tbody></table></div>
        <div className="dialog-actions"><button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.max(1, Math.ceil(events.length / 30))}</span><button className="btn" disabled={(page + 1) * 30 >= events.length} onClick={() => setPage(page + 1)}>下一页</button></div>
      </details>
    </>}
    <details className="section-gap"><summary>逐窗样本诊断：为什么不足 30 笔？</summary>
      {(result.wf?.wf_detail || []).map((row, index) => <section key={row.window || index} className="metric-compare-block"><h4>{row.window || `窗口 ${index + 1}`}</h4>
        {([['训练', row.train_diagnostic], ['测试', row.test_diagnostic]] as const).map(([label, diagnostic]) => <p key={label}>{label}：{diagnostic ? `可回放交易 ${diagnostic.replay_trades} → 账户买入 ${diagnostic.entries ?? 0} → 完成交易 ${diagnostic.completed_trades}，最低 ${diagnostic.minimum_trades} 笔。${diagnostic.message}` : '历史任务未保存过滤前数量，不能把旧报告的 0 直接解释为没有信号。'}{diagnostic?.rejection_counts && <small> 拒绝原因：{Object.entries(diagnostic.rejection_counts).map(([code, count]) => `${code} ${count}`).join('、') || '无'}</small>}</p>)}
      </section>)}
      {!result.wf?.wf_detail?.length && <p>没有滚动窗口证据，不能宣称稳定性通过。</p>}
    </details>
  </section>
}
