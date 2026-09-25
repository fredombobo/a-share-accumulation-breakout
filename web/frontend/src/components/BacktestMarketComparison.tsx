import { useState } from 'react'
import type { EChartsOption } from 'echarts'
import type { BacktestMarketComparison as Comparison } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'

const percent = (value: number | null | undefined) => value == null ? '未记录' : `${(value * 100).toFixed(2)}%`
const points = (value: number | null | undefined) => value == null ? '未记录' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)} 个百分点`

export default function BacktestMarketComparison({ comparison }: { comparison?: Comparison }) {
  const colors = useChartColors()
  const [scope, setScope] = useState<'is' | 'oos'>('oos')
  const [view, setView] = useState<'nav' | 'excess' | 'drawdown'>('nav')
  const row = comparison?.[scope]
  const usable = comparison?.status === 'COMPLETE' && row
  const curve = row?.curve || []
  const option: EChartsOption = {
    animation: false,
    grid: { left: 68, right: 28, top: 55, bottom: 65 },
    tooltip: { trigger: 'axis', valueFormatter: value => `${Number(value).toFixed(2)}${view === 'nav' ? '' : view === 'excess' ? ' 个百分点' : '%'}` },
    legend: { top: 4, textStyle: { color: colors.text } },
    xAxis: { type: 'category', data: [row?.anchor_date || '', ...curve.map(point => point.trade_date)], axisLabel: { color: colors.subtext }, axisLine: { lineStyle: { color: colors.axis } } },
    yAxis: { type: 'value', scale: true, name: view === 'nav' ? '期初 = 1' : view === 'excess' ? '百分点' : '回撤 %', nameTextStyle: { color: colors.subtext }, axisLabel: { color: colors.subtext }, splitLine: { lineStyle: { color: colors.split } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8, textStyle: { color: colors.subtext } }],
    series: view === 'excess' ? [{ name: '策略减沪深300', type: 'line', showSymbol: false, smooth: false,
      data: [0, ...curve.map(point => point.excess_return * 100)], lineStyle: { color: colors.accent },
      markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], label: { formatter: '持平' } },
    }] : [
      { name: '策略（扣费）', type: 'line', showSymbol: false, smooth: false, lineStyle: { color: colors.accent },
        data: [view === 'nav' ? 1 : 0, ...curve.map(point => view === 'nav' ? point.strategy_nav : -point.strategy_drawdown * 100)] },
      { name: '沪深300（价格）', type: 'line', showSymbol: false, smooth: false, lineStyle: { color: colors.warn, type: 'dashed' },
        data: [view === 'nav' ? 1 : 0, ...curve.map(point => view === 'nav' ? point.benchmark_nav : -point.benchmark_drawdown * 100)] },
    ],
  }
  return <section className="card section-gap" aria-label="有没有跑赢大盘">
    <div className="h-sec"><h2>有没有跑赢大盘？</h2>
      <div className="seg" role="group" aria-label="大盘对照区间">
        {(['is', 'oos'] as const).map(key => <button type="button" key={key} aria-pressed={scope === key} className={`seg-item${scope === key ? ' on' : ''}`} onClick={() => setScope(key)}>{key === 'is' ? '样本内' : '样本外'}</button>)}
      </div>
    </div>
    {!usable ? <p role="status">{comparison?.reason || '该历史报告未记录同一快照的大盘对照，不能据此宣称跑赢大盘。新建回测将记录，不补写旧报告。'}</p> : <>
      <p>{row.start} — {row.end} · {row.sessions} 个交易日 · {row.excess_return > 0 ? '该区间超过沪深300价格涨幅' : '该区间未超过沪深300价格涨幅'}，不等于已证明持续选股能力。</p>
      <div className="metric-compare">
        <div className="metric-compare-block"><h4>策略净收益 / 大盘价格涨幅</h4><p className="num">{percent(row.strategy_return)} / {percent(row.benchmark_return)}</p></div>
        <div className="metric-compare-block"><h4>收益差 / 2 倍成本后的收益差</h4><p className="num">{points(row.excess_return)} / {points(row.stress_excess_return)}</p></div>
        <div className="metric-compare-block"><h4>策略 / 大盘最大回撤</h4><p className="num">{percent(row.strategy_max_drawdown)} / {percent(row.benchmark_max_drawdown)}</p></div>
      </div>
      <div className="seg section-gap" role="group" aria-label="大盘对照图表">
        {(['nav', 'excess', 'drawdown'] as const).map(key => <button type="button" key={key} aria-pressed={view === key} className={`seg-item${view === key ? ' on' : ''}`} onClick={() => setView(key)}>{{ nav: '同期净值', excess: '累计收益差', drawdown: '回撤对照' }[key]}</button>)}
      </div>
      <EChart option={option} height={320} />
      <div className="table-scroll section-gap"><table className="data"><thead><tr><th>滚动测试段</th><th>策略净收益</th><th>大盘价格涨幅</th><th>收益差</th><th>成交数</th></tr></thead><tbody>
        {(comparison.wf || []).map(window => <tr key={window.window}><td>{window.window}</td><td>{percent(window.strategy_return)}</td><td>{percent(window.benchmark_return)}</td><td>{points(window.excess_return)}</td><td>{window.test_n ?? '未记录'}</td></tr>)}
      </tbody></table></div>
    </>}
    <p className="section-gap">{comparison?.notice || '固定参考：沪深300价格指数。随机和均线基线不是大盘。'}</p>
  </section>
}
