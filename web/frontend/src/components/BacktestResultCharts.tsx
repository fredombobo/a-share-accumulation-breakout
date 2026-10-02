import { useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import type { EChartsOption } from 'echarts'
import type { BacktestMetrics, BacktestResult } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import { finiteMetric, portfolioMaxDrawdown, portfolioProfitFactor } from './backtestMetricContract'
import { buildAtlasData, type AtlasScope } from './backtestAtlasData'
import EChart from './EChart'
import './BacktestResultCharts.css'

type View = 'performance' | 'trades' | 'robustness'
const views: { id: View; label: string; subtitle: string }[] = [
  { id: 'performance', label: '业绩与风险', subtitle: '收益路径 · 月度表现' },
  { id: 'trades', label: '交易画像', subtitle: '资金使用 · 成交成本' },
  { id: 'robustness', label: '参数稳健性', subtitle: '参数比较 · 跨窗检验' },
]
// A per-trade mean is never an account's cumulative return.
const totalReturn = (m?: BacktestMetrics | null) => finiteMetric(m?.portfolio_total_return ?? m?.net_total_return)
const pct = (n: number | null, signed = false) => n == null ? '—' : `${signed && n > 0 ? '+' : ''}${n.toFixed(2)}%`
const dec = (n: number | null) => n == null ? '—' : n.toFixed(2)
const money = (n: number | null) => n == null ? '—' : n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dateLabel = (d: string) => /^\d{8}$/.test(d) ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d
const tone = (n: number | null) => n == null || n === 0 ? '' : n > 0 ? 'atlas-positive' : 'atlas-negative'

function Empty({ children }: { children: ReactNode }) {
  return <div className="atlas-empty"><span className="atlas-empty-glyph" aria-hidden="true">∅</span><strong>暂无可绘制数据</strong><p>{children}</p></div>
}
function Panel({ number, title, note, aside, children, className = '' }: {
  number: string; title: string; note: string; aside?: ReactNode; children: ReactNode; className?: string
}) {
  return <section className={`atlas-panel ${className}`} aria-label={title}>
    <header className="atlas-panel-head"><div><span className="atlas-index">{number}</span><h3>{title}</h3><p>{note}</p></div>{aside && <div className="atlas-panel-aside">{aside}</div>}</header>
    {children}
  </section>
}

export default function BacktestResultCharts({ result, reportIdentity }: {
  result: BacktestResult; reportIdentity?: { taskId: string; codeVersion: string }
}) {
  const colors = useChartColors()
  const [view, setView] = useState<View>('performance')
  const [scope, setScope] = useState<AtlasScope>('oos')
  const data = useMemo(() => buildAtlasData(result, scope), [result, scope])
  const metrics = result.selected?.[scope]
  const netReturn = totalReturn(metrics)
  const drawdown = portfolioMaxDrawdown(metrics)
  const nTrades = finiteMetric(metrics?.net_n_trades)
  const market = result.market_comparison?.status === 'COMPLETE' ? result.market_comparison[scope] : undefined
  const marketCurve = market?.curve || []
  const hasMarket = marketCurve.some(row => finiteMetric(row.strategy_nav) != null)
  const dates = hasMarket ? marketCurve.map(row => row.trade_date) : data.curve.map(row => row.date)
  const nav = hasMarket ? marketCurve.map(row => finiteMetric(row.strategy_nav)) : data.curve.map(row => row.nav)
  const dd = hasMarket ? marketCurve.map(row => { const n = finiteMetric(row.strategy_drawdown); return n == null ? null : -Math.abs(n) * 100 }) : data.curve.map(row => row.drawdownPct == null ? null : -row.drawdownPct)
  const benchmark = result.market_comparison?.benchmark_name || '基准指数'
  const window = result.request.windows[scope]
  const independent = Boolean(result.path_analysis?.evidence_complete && result.independent_leaderboard?.length)
  const rankedRows = independent ? result.independent_leaderboard! : result.leaderboard
  const topRows = rankedRows.slice(0, 10)
  const wf = result.wf?.wf_detail || []

  const axis = { axisLabel: { color: colors.subtext, fontSize: 10 }, axisLine: { lineStyle: { color: colors.axis } }, axisTick: { show: false }, splitLine: { lineStyle: { color: colors.split, type: 'dashed' as const } } }
  const base = { animationDuration: 300, color: colors.palette, textStyle: { color: colors.text, fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif' }, tooltip: { trigger: 'axis' as const, renderMode: 'richText' as const, confine: true, backgroundColor: 'rgba(30,42,58,.96)', borderWidth: 0, textStyle: { color: '#f3f6fa', fontSize: 12 } } }
  const navOption: EChartsOption | null = nav.some(n => n != null) ? {
    ...base,
    legend: { top: 8, left: 26, icon: 'roundRect', itemWidth: 16, itemHeight: 3, textStyle: { color: colors.subtext, fontSize: 11 }, data: ['策略净值', ...(hasMarket ? [benchmark] : []), '策略回撤'] },
    grid: [{ left: 58, right: 26, top: 50, height: '52%' }, { left: 58, right: 26, top: '72%', height: '15%' }],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    xAxis: [
      { ...axis, type: 'category', data: dates.map(dateLabel), boundaryGap: false, axisLabel: { show: false }, splitLine: { show: false } },
      { ...axis, gridIndex: 1, type: 'category', data: dates.map(dateLabel), boundaryGap: false, axisLabel: { color: colors.subtext, fontSize: 10, hideOverlap: true, formatter: (v: string) => v.slice(2) }, splitLine: { show: false } },
    ],
    yAxis: [{ ...axis, type: 'value', scale: true, axisLabel: { color: colors.subtext, fontSize: 10, formatter: (v: number) => v.toFixed(2) } }, { ...axis, gridIndex: 1, type: 'value', max: 0, axisLabel: { color: colors.subtext, fontSize: 10, formatter: '{value}%' }, splitNumber: 2 }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }, { type: 'slider', xAxisIndex: [0, 1], height: 12, bottom: 2, showDetail: false, borderColor: 'transparent', backgroundColor: colors.split, fillerColor: `${colors.accent}22`, handleStyle: { color: colors.accent, borderWidth: 0 }, dataBackground: { lineStyle: { color: colors.axis }, areaStyle: { color: colors.split } } }],
    series: [
      { name: '策略净值', type: 'line', data: nav, symbol: 'none', connectNulls: false, lineStyle: { width: 2.4, color: colors.accent }, itemStyle: { color: colors.accent }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${colors.accent}22` }, { offset: 1, color: `${colors.accent}00` }] } }, markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: colors.axis, type: 'dashed' }, data: [{ yAxis: 1 }] } },
      ...(hasMarket ? [{ name: benchmark, type: 'line' as const, data: marketCurve.map(row => finiteMetric(row.benchmark_nav)), connectNulls: false, symbol: 'none', itemStyle: { color: colors.warn }, lineStyle: { width: 1.5, color: colors.warn, type: 'dashed' as const } }] : []),
      { name: '策略回撤', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: dd, connectNulls: false, symbol: 'none', lineStyle: { width: 1, color: colors.down }, areaStyle: { color: `${colors.down}35` }, itemStyle: { color: colors.down }, tooltip: { valueFormatter: v => `${Number(v).toFixed(2)}%` } },
    ],
  } : null

  const comparison = [
    { label: '入选策略', m: result.selected?.oos },
    { label: `${result.cost_stress?.multiplier || '压力'} 成本`, m: result.cost_stress?.metrics },
    { label: '随机基线', m: result.baselines?.random },
    { label: 'MA20/60', m: result.baselines?.ma20_60 },
  ].filter(row => row.m != null)
  const comparable = comparison.filter(row => totalReturn(row.m) != null)
  const comparisonOption: EChartsOption | null = comparable.length ? {
    ...base, grid: { left: 92, right: 64, top: 22, bottom: 32 },
    xAxis: { ...axis, type: 'value', axisLabel: { color: colors.subtext, fontSize: 10, formatter: '{value}%' } },
    yAxis: { ...axis, type: 'category', inverse: true, data: comparable.map(row => row.label), splitLine: { show: false } },
    series: [{ type: 'bar', barMaxWidth: 9, data: comparable.map(row => ({ value: totalReturn(row.m)! * 100, itemStyle: { color: totalReturn(row.m)! >= 0 ? colors.up : colors.down, borderRadius: 2 } })), label: { show: true, position: 'right', color: colors.text, fontSize: 11, formatter: p => pct(Number(p.value), true) }, tooltip: { valueFormatter: v => pct(Number(v), true) } }],
  } : null

  const exposureOption: EChartsOption | null = data.curve.some(p => p.exposurePct != null || p.cashPct != null) ? {
    ...base, legend: { top: 8, left: 26, textStyle: { color: colors.subtext } }, grid: { left: 58, right: 26, top: 48, bottom: 40 },
    xAxis: { ...axis, type: 'category', data: data.curve.map(p => dateLabel(p.date)), boundaryGap: false, axisLabel: { color: colors.subtext, fontSize: 10, hideOverlap: true, formatter: (v: string) => v.slice(2) }, splitLine: { show: false } },
    yAxis: { ...axis, type: 'value', min: 0, axisLabel: { color: colors.subtext, formatter: '{value}%' } },
    series: [{ name: '股票市值 / 权益', type: 'line', data: data.curve.map(p => p.exposurePct), connectNulls: false, symbol: 'none', itemStyle: { color: colors.accent }, areaStyle: { color: `${colors.accent}22` }, lineStyle: { width: 2 } }, { name: '现金 / 权益', type: 'line', data: data.curve.map(p => p.cashPct), connectNulls: false, symbol: 'none', itemStyle: { color: colors.accent2 }, lineStyle: { width: 1.4, type: 'dashed' } }],
    tooltip: { ...base.tooltip, valueFormatter: v => pct(Number(v)) },
  } : null
  const exitOption: EChartsOption | null = data.exits.length ? {
    ...base, grid: { left: 75, right: 24, top: 30, bottom: 64 },
    xAxis: { ...axis, type: 'category', data: data.exits.map((p, i) => `${i + 1} · ${p.code}\n${dateLabel(p.date)}`), axisLabel: { color: colors.subtext, fontSize: 10, interval: data.exits.length <= 8 ? 0 : 'auto', hideOverlap: true }, splitLine: { show: false } },
    yAxis: { ...axis, type: 'value', name: '净损益 / 元', nameTextStyle: { color: colors.subtext, padding: [0, 0, 0, 20] } },
    series: [{ type: 'bar', barMaxWidth: 28, data: data.exits.map(p => ({ value: p.pnlYuan, itemStyle: { color: p.pnlYuan >= 0 ? colors.up : colors.down, borderRadius: 2 } })), markLine: { silent: true, symbol: 'none', label: { show: false }, data: [{ yAxis: 0 }], lineStyle: { color: colors.axis } }, tooltip: { valueFormatter: v => `${money(Number(v))} 元` } }],
    ...(data.exits.length > 16 ? { dataZoom: [{ type: 'inside', startValue: 0, endValue: 15 }] } : {}),
  } : null
  const feeOption: EChartsOption | null = data.cashFees.yuan != null && data.cashFees.yuan > 0 ? {
    ...base, tooltip: { ...base.tooltip, trigger: 'item', valueFormatter: v => `${money(Number(v))} 元` },
    series: [{ name: '现金费用', type: 'pie', radius: ['62%', '80%'], center: ['50%', '50%'], avoidLabelOverlap: true, label: { show: false }, emphasis: { scaleSize: 4, label: { show: false } }, itemStyle: { borderWidth: 3, borderColor: 'transparent', borderRadius: 3 }, data: data.fees.map((p, i) => ({ name: p.label, value: p.yuan, itemStyle: { color: colors.palette[i] } })) }],
    graphic: [{ type: 'text', left: 'center', top: '40%', style: { text: money(data.cashFees.yuan), fill: colors.text, font: '600 25px Segoe UI' } }, { type: 'text', left: 'center', top: '56%', style: { text: '实际现金费用 · 元', fill: colors.subtext, font: '11px Microsoft YaHei' } }],
  } : null
  const scatterRows = rankedRows.filter(row => totalReturn(row.oos) != null && portfolioMaxDrawdown(row.oos) != null)
  const scatterOption: EChartsOption | null = scatterRows.length ? {
    ...base, tooltip: { ...base.tooltip, trigger: 'item', formatter: p => { const value = (Array.isArray(p) ? p[0] : p).value as number[]; return [`原 IS 排名 ${value[3]}`, `OOS 回撤 ${pct(value[0])}`, `OOS 收益 ${pct(value[1], true)}`, `完成交易 ${value[2] < 0 ? '未记录' : value[2]}`].join('\n') } },
    grid: { left: 64, right: 32, top: 32, bottom: 48 },
    xAxis: { ...axis, type: 'value', name: '最大回撤 %', nameLocation: 'middle', nameGap: 29, nameTextStyle: { color: colors.subtext }, min: 0 },
    yAxis: { ...axis, type: 'value', name: '净收益 %', nameTextStyle: { color: colors.subtext }, scale: true },
    series: [{ type: 'scatter', symbolSize: (v: number[]) => 12 + Math.min(Math.sqrt(Math.max(0, v[2])) * 2, 20), data: scatterRows.map(row => ({ value: [portfolioMaxDrawdown(row.oos)! * 100, totalReturn(row.oos)! * 100, finiteMetric(row.oos.net_n_trades) ?? -1, rankedRows.indexOf(row) + 1], itemStyle: { color: row.param_id === result.selected?.param_id ? colors.warn : colors.accent, opacity: .8, borderWidth: row.param_id === result.selected?.param_id ? 2 : 0, borderColor: colors.text }, label: { show: scatterRows.length <= 8 || row.param_id === result.selected?.param_id, formatter: row.param_id === result.selected?.param_id ? '入选参数' : `#${rankedRows.indexOf(row) + 1}`, position: 'top', color: colors.subtext, fontSize: 10 } })), markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: colors.axis }, data: [{ yAxis: 0 }] } }],
  } : null
  const wfOption: EChartsOption | null = wf.some(row => finiteMetric(row.train_pf) != null || finiteMetric(row.test_pf) != null) ? {
    ...base, grid: { left: 48, right: 30, top: 38, bottom: 36 }, legend: { top: 0, textStyle: { color: colors.subtext } },
    xAxis: { ...axis, type: 'category', data: wf.map((r, i) => r.window || `WF${i + 1}`), splitLine: { show: false } },
    yAxis: { ...axis, type: 'value', min: 0, name: 'PF', nameTextStyle: { color: colors.subtext } },
    series: [{ name: '训练 PF', type: 'scatter', symbolSize: 10, data: wf.map(r => finiteMetric(r.train_pf)), itemStyle: { color: colors.accent }, markLine: { silent: true, symbol: 'none', label: { formatter: 'PF = 1', color: colors.subtext }, lineStyle: { color: colors.axis, type: 'dashed' }, data: [{ yAxis: 1 }] } }, { name: '测试 PF', type: 'scatter', symbol: 'diamond', symbolSize: 12, data: wf.map(r => finiteMetric(r.test_pf)), itemStyle: { color: colors.warn } }],
  } : null

  const monthMap = new Map(data.months.map(row => [row.month, row.returnPct]))
  const years = [...new Set(data.months.map(row => row.month.slice(0, 4)))].sort()
  const maxMonth = Math.max(.01, ...data.months.map(row => Math.abs(row.returnPct ?? 0)))
  const prefix = scope === 'oos' ? '样本外' : '样本内'
  const canCompare = scope === 'oos'

  return <section className="result-atlas section-gap" aria-labelledby="result-visuals-title">
    <header className="atlas-heading"><div><div className="atlas-kicker">RESEARCH ATLAS <span> / </span> 回测结果分析</div><h2 id="result-visuals-title">结果图谱</h2><p>沿着资金路径，查看收益的来源与代价。</p></div><div className="atlas-scope" role="group" aria-label="图谱数据区间">{(['is', 'oos'] as const).map(s => <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>{s.toUpperCase()} <span>{s === 'is' ? '样本内' : '样本外'}</span></button>)}</div></header>
    <div className="atlas-context"><span className="atlas-scope-label">{scope.toUpperCase()} · {prefix}</span><span>{dateLabel(window[0])} — {dateLabel(window[1])}</span><span className="atlas-context-verdict">{result.verdict_label}</span></div>
    <div className="atlas-stat-strip" aria-label="图谱区间摘要">
      <div><span>组合净收益</span><strong className={tone(netReturn)}>{pct(netReturn == null ? null : netReturn * 100, true)}</strong><small>按账户权益计算</small></div>
      <div><span>最大回撤</span><strong>{pct(drawdown == null ? null : drawdown * 100)}</strong><small>区间权益峰值至谷底</small></div>
      <div><span>完成交易</span><strong>{nTrades ?? '—'}<i>笔</i></strong><small>实际成交统计</small></div>
      <div><span>平均股票仓位</span><strong>{pct(data.metrics.averageExposurePct)}</strong><small>{data.metrics.exposureDays} 个有效账户日</small></div>
    </div>
    <div className="atlas-tabs" role="tablist" aria-label="结果分析视图">{views.map((tab, index) => <button type="button" role="tab" key={tab.id} id={`atlas-tab-${tab.id}`} aria-controls="atlas-view" aria-selected={view === tab.id} onClick={() => setView(tab.id)} onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3; setView(views[next].id); document.getElementById(`atlas-tab-${views[next].id}`)?.focus() }} tabIndex={view === tab.id ? 0 : -1}><span className="atlas-tab-number">0{index + 1}</span><span><b>{tab.label}</b><small>{tab.subtitle}</small></span></button>)}</div>
    <div id="atlas-view" role="tabpanel" aria-labelledby={`atlas-tab-${view}`} tabIndex={0}>
      {view === 'performance' && <div className="atlas-performance-grid">
        <Panel number="01" title="净值与回撤" note={hasMarket ? `同一基准日归一化 · ${dates.length} 个交易日` : '使用已保存账户净值；没有同期基准时仅展示策略'} className="atlas-main-chart" aside={<span className="atlas-unit">净值起点 1.00</span>}>
          {navOption ? <EChart option={navOption} height={380} ariaLabel={`${prefix}策略净值与回撤${hasMarket ? `，对照${benchmark}` : ''}`} /> : <Empty>这份报告没有保存所选区间的有效权益路径。</Empty>}
          <p className="atlas-caption">{hasMarket ? `${benchmark}为未扣费价格指数，策略为净收益；曲线沿用报告基准日 ${dateLabel(market!.anchor_date)}。` : '账户明细从首次记录日起展示，不把缺失的早期账户日补零。'} 下方阴影为回撤，拖动底部时间条可缩放区间。</p>
        </Panel>
        <aside className="atlas-reading" aria-label="图谱阅读提示"><span className="atlas-kicker">READ THE EVIDENCE</span><h3>收益之外，<br />还有多少证据？</h3><div className="atlas-reading-number">{nTrades ?? '—'}<span>笔完成交易</span></div><p>{nTrades == null ? '历史报告未记录完成交易数。' : nTrades < 30 ? '当前成交样本较少，单笔交易会明显影响收益和盈亏比。' : '结合独立路径和跨窗口结果，检查表现是否依赖少数交易。'}</p><dl><div><dt>有持仓的账户日</dt><dd>{data.metrics.investedDays ?? '—'} / {data.metrics.exposureDays}</dd></div><div><dt>盈利月份 / 已记录</dt><dd>{data.metrics.winningMonths ?? '—'} / {data.metrics.availableMonths}</dd></div><div><dt>回撤中的账户日</dt><dd>{data.metrics.underwaterDays ?? '—'}</dd></div><div><dt>Profit Factor</dt><dd>{dec(portfolioProfitFactor(metrics))}</dd></div></dl><p className="atlas-reading-foot">仓位与天数来自已记录的账户区间；低仓位也可能带来较小回撤。</p></aside>
        <Panel number="02" title="月度收益日历" note="颜色表示方向与幅度，悬停或聚焦查看具体月份" className="atlas-month-panel" aside={<span className="atlas-unit">单位 %</span>}>
          {years.length ? <><div className="atlas-calendar-scroll"><div className="atlas-calendar" role="table" aria-label={`${prefix}月度收益热力表`}><div role="row" className="atlas-calendar-row atlas-calendar-head"><span role="columnheader">年度</span>{Array.from({ length: 12 }, (_, i) => <span role="columnheader" key={i}>{i + 1}月</span>)}</div>{years.map(year => <div className="atlas-calendar-row" role="row" key={year}><strong role="rowheader">{year}</strong>{Array.from({ length: 12 }, (_, i) => { const month = `${year}${String(i + 1).padStart(2, '0')}`; const value = monthMap.get(month) ?? null; const label = `${year}年${i + 1}月：${value == null ? '未记录' : pct(value, true)}`; return <span role="cell" key={month} tabIndex={0} aria-label={label} title={label} className={`atlas-month ${value == null ? 'atlas-month-missing' : value === 0 ? 'atlas-month-flat' : value > 0 ? 'atlas-month-gain' : 'atlas-month-loss'}`} style={{ '--month-alpha': value == null || value === 0 ? 0 : .09 + .35 * Math.abs(value) / maxMonth } as CSSProperties}>{value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}`}</span> })}</div>)}</div></div><div className="atlas-calendar-key"><span><i className="atlas-key-loss" />负收益</span><span><i className="atlas-key-gain" />正收益</span><span><i className="atlas-key-missing" />未记录</span></div><p className="atlas-caption">缺失月份留空；首尾月可能不完整。账户记录范围 {data.curve.length ? `${dateLabel(data.curve[0].date)} 至 ${dateLabel(data.curve.at(-1)!.date)}` : '未记录'}。</p></> : <Empty>没有月度账户结算数据，不从交易均值推算。</Empty>}
        </Panel>
        <Panel number="03" title="OOS 基线对照" note="同为样本外的组合净收益；策略、成本压力与基线分开标识">
          {canCompare ? <>{comparisonOption ? <EChart option={comparisonOption} height={230} ariaLabel="样本外策略与基线净收益对照" /> : <Empty>报告缺少可比较的组合净收益。</Empty>}<div className="atlas-mini-table"><table><thead><tr><th>方案</th><th>完成交易</th><th>PF</th></tr></thead><tbody>{comparison.map(row => <tr key={row.label}><td>{row.label}</td><td>{finiteMetric(row.m?.net_n_trades) ?? '—'}</td><td>{dec(portfolioProfitFactor(row.m))}</td></tr>)}</tbody></table></div></> : <Empty>基线与成本压力仅保存 OOS 结果。切换到样本外查看，避免混用不同区间。</Empty>}
        </Panel>
      </div>}
      {view === 'trades' && <div className="atlas-trade-grid">
        <Panel number="01" title="资金使用轨迹" note="股票市值与现金分别占账户权益的比例" className="atlas-wide" aside={<span className="atlas-unit">{data.metrics.curveDays} 个账户日</span>}>{exposureOption ? <EChart option={exposureOption} height={310} ariaLabel={`${prefix}股票仓位与现金比例走势`} /> : <Empty>没有足够的权益、现金与持仓市值记录。</Empty>}<p className="atlas-caption">仅使用所选区间的真实账户日，不将交易前缺失的日期视为空仓。</p></Panel>
        <Panel number="02" title="逐次卖出损益" note="每一柱对应一条实际卖出成交事件，包含已分摊成本" aside={<span className="atlas-count">{data.exits.length} 次卖出</span>}>{exitOption ? <EChart option={exitOption} height={300} ariaLabel={`${prefix}实际卖出事件净损益，单位元`} /> : <Empty>没有可核验的卖出成交损益。</Empty>}<p className="atlas-caption">部分卖出也单独计为一个事件，不等同于独立完整交易。{!data.availability.exits.complete && data.availability.exits.expected > 0 ? `有效损益覆盖 ${data.availability.exits.available}/${data.availability.exits.expected}，缺失项未绘制。` : '按成交日期呈现，不做分布拟合。'}</p></Panel>
        <Panel number="03" title="实际费用构成" note="现金实际扣费；滑点单独展示其影响" className="atlas-fee-panel">{feeOption ? <EChart option={feeOption} height={220} ariaLabel={`${prefix}佣金、印花税与其他现金费用构成`} /> : data.cashFees.yuan === 0 ? <div className="atlas-zero-fee"><strong>0.00</strong><span>已记录现金费用为零</span></div> : <Empty>费用记录不完整，暂不汇总或计算占比。</Empty>}<div className="atlas-fee-list">{data.fees.map((fee, i) => <div key={fee.key}><span><i style={{ background: colors.palette[i] }} />{fee.label}</span><strong>{money(fee.yuan)}<small> 元</small></strong></div>)}<div className="atlas-slippage"><span>成交价中的滑点</span><strong>{money(data.slippage.yuan)}<small> 元</small></strong></div></div><p className="atlas-caption">滑点已体现在成交价格中，不再加入现金费用重复扣除。缺失字段显示为「—」。</p></Panel>
        <Panel number="04" title="成交事件概览" note="按原始日志事件计数，同一订单可能产生多个事件" className="atlas-wide">{data.availability.events ? <div className="atlas-event-grid">{data.events.map((event, i) => <div key={event.label}><span className="atlas-event-index">0{i + 1}</span><strong>{event.count}</strong><span>{event.label}</span><div className="atlas-event-track"><i style={{ width: `${event.count === 0 ? 0 : event.count / Math.max(1, ...data.events.map(p => p.count)) * 100}%`, background: i < 2 ? colors.accent : colors.warn }} /></div></div>)}</div> : <Empty>这份报告没有保存成交事件日志。</Empty>}</Panel>
      </div>}
      {view === 'robustness' && <div className="atlas-robust-grid">
        <div className="atlas-research-note atlas-wide"><span className="atlas-count">{rankedRows.length} {independent ? '条已核验路径' : '组名义参数'}</span><p>{rankedRows.length < 2 ? '当前仅有一组结果，尚不能判断参数敏感性。' : '保留原样本内排名，观察样本外表现是否一致。'} {independent ? '路径按报告中已核验的权益证据去重。' : '这份报告缺少完整路径证据，名义参数没有按独立路径去重。'}</p></div>
        <Panel number="01" title="参数风险收益分布" note="横轴为 OOS 回撤，纵轴为 OOS 净收益；点越大代表完成交易越多" aside={<span className="atlas-unit">金色为入选参数</span>}>{scatterOption ? <EChart option={scatterOption} height={310} ariaLabel="参数的样本外风险收益散点图" /> : <Empty>参数缺少组合收益或最大回撤，无法定位。</Empty>}</Panel>
        <Panel number="02" title="WF 窗口稳定性" note="训练与测试的 PF 分窗展示；PF = 1 为盈亏相抵，不连接缺失点">{wfOption ? <EChart option={wfOption} height={200} ariaLabel="Walk-forward 训练与测试窗口 Profit Factor 点图" /> : <Empty>窗口没有可计算的 PF；仍保留下方样本记录。</Empty>}<div className="atlas-mini-table"><table><thead><tr><th>窗口</th><th>训练 PF / 笔数</th><th>测试 PF / 笔数</th></tr></thead><tbody>{wf.map((row, i) => <tr key={`${row.window}-${i}`}><td>{row.window || `WF${i + 1}`}</td><td>{dec(finiteMetric(row.train_pf))} / {row.train_n ?? '—'}</td><td>{dec(finiteMetric(row.test_pf))} / {row.test_n ?? '—'}</td></tr>)}</tbody></table></div><p className="atlas-caption">「—」表示 PF 未定义或缺少数据；零成交与有成交但无亏损分母不能按 PF = 0 处理。</p></Panel>
        <Panel number="03" title={independent ? '独立路径表现矩阵' : '历史名义参数表现矩阵'} note="前十名沿用原 IS 排名，不按 OOS 重新挑选；此页比较始终使用完整 IS / OOS" className="atlas-wide"><div className="atlas-matrix-scroll"><table className="atlas-matrix"><thead><tr><th>IS 排名 / 参数</th><th>IS 净收益</th><th>OOS 净收益</th><th>OOS 最大回撤</th><th>OOS PF</th><th>OOS 交易</th></tr></thead><tbody>{topRows.map((row, i) => <tr key={`${row.param_id}-${i}`} className={row.param_id === result.selected?.param_id ? 'atlas-selected-row' : ''}><td><span className="atlas-rank">{String(i + 1).padStart(2, '0')}</span><code title={row.param_id}>{row.param_id.slice(0, 12)}</code>{row.param_id === result.selected?.param_id && <em>入选</em>}</td><td className={tone(totalReturn(row.is))}>{pct(totalReturn(row.is) == null ? null : totalReturn(row.is)! * 100, true)}</td><td className={tone(totalReturn(row.oos))}>{pct(totalReturn(row.oos) == null ? null : totalReturn(row.oos)! * 100, true)}</td><td>{pct(portfolioMaxDrawdown(row.oos) == null ? null : portfolioMaxDrawdown(row.oos)! * 100)}</td><td>{dec(portfolioProfitFactor(row.oos))}</td><td>{finiteMetric(row.oos.net_n_trades) ?? '—'}</td></tr>)}</tbody></table>{!topRows.length && <Empty>没有保存参数排行榜。</Empty>}</div></Panel>
      </div>}
    </div>
    <footer className="atlas-footer"><span>{reportIdentity ? `历史任务 ${reportIdentity.taskId} · 代码 ${reportIdentity.codeVersion}` : '图谱来自当前所选历史报告'}；切换视图不会重算结果。</span><span>缺失留空 · 红涨绿跌 · 金额单位元</span></footer>
    {data.errors.length > 0 && <details className="atlas-data-notes"><summary>数据完整性说明 · {data.errors.length} 项</summary><ul>{[...new Set(data.errors.map(error => error.message))].map(message => <li key={message}>{message}</li>)}</ul></details>}
  </section>
}
