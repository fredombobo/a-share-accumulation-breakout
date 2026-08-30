import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'

import type { BacktestMetrics, BacktestResult, BacktestWalkForwardWindow } from '../api/client'
import EChart from './EChart'
import {
  finiteMetric,
  portfolioMaxDrawdown,
  portfolioProfitFactor,
  portfolioTotalReturn,
} from './backtestMetricContract'
import { useChartColors } from '../theme/ThemeContext'

function percent(value: number): string {
  return `${value.toFixed(2)}%`
}

function ResultChartPanel({
  title,
  description,
  sample,
  option,
  height = 300,
  empty,
}: {
  title: string
  description: string
  sample: string
  option: EChartsOption | null
  height?: number
  empty: string
}) {
  return (
    <section className="result-chart-panel" aria-label={title}>
      <header>
        <div><h3>{title}</h3><p>{description}</p></div>
        <span>{sample}</span>
      </header>
      {option ? <EChart option={option} height={height} /> : <div className="chart-empty"><b>暂无可绘制数据</b><span>{empty}</span></div>}
    </section>
  )
}

function wfRows(result: BacktestResult): BacktestWalkForwardWindow[] {
  return (result.wf?.wf_detail || []).filter((row) => (
    finiteMetric(row.train_pf) != null || finiteMetric(row.test_pf) != null || row.test_n === 0
  ))
}

export default function BacktestResultCharts({ result }: { result: BacktestResult }) {
  const colors = useChartColors()
  const selected = result.selected
  const rankedRows = result.independent_leaderboard || result.leaderboard

  const comparison = useMemo(() => {
    const rows: { label: string; value: number }[] = []
    const add = (label: string, metrics: BacktestMetrics | null | undefined) => {
      const value = portfolioTotalReturn(metrics)
      if (value != null) rows.push({ label, value: value * 100 })
    }
    add('IS 样本内', selected?.is)
    add('OOS 样本外', selected?.oos)
    add('2 倍成本', result.cost_stress?.metrics)
    add('随机基线', result.baselines?.random)
    add('MA20/60', result.baselines?.ma20_60)
    return rows
  }, [result, selected])

  const comparisonOption = useMemo<EChartsOption | null>(() => {
    if (!comparison.length) return null
    return {
      animationDuration: 260,
      grid: { left: 58, right: 18, top: 22, bottom: 52 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value) => percent(Number(value)),
      },
      xAxis: {
        type: 'category', data: comparison.map((row) => row.label),
        axisLabel: { color: colors.subtext, interval: 0, rotate: comparison.length > 4 ? 18 : 0 },
        axisLine: { lineStyle: { color: colors.axis } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value', name: '净收益 (%)', nameTextStyle: { color: colors.subtext },
        axisLabel: { color: colors.subtext, formatter: '{value}%' },
        splitLine: { lineStyle: { color: colors.split } },
      },
      series: [{
        type: 'bar', barMaxWidth: 44,
        data: comparison.map((row) => ({
          value: row.value,
          itemStyle: { color: row.value >= 0 ? colors.up : colors.down, borderRadius: row.value >= 0 ? [5, 5, 0, 0] : [0, 0, 5, 5] },
        })),
        label: { show: true, position: 'top', color: colors.text, formatter: ({ value }: { value?: unknown }) => percent(Number(value)) },
      }],
    }
  }, [colors, comparison])

  const qualityComparison = useMemo(() => {
    const rows: { label: string; value: number }[] = []
    const add = (label: string, metrics: BacktestMetrics | null | undefined) => {
      const value = portfolioProfitFactor(metrics)
      if (value != null) rows.push({ label, value })
    }
    add('IS 样本内', selected?.is)
    add('OOS 样本外', selected?.oos)
    add('2 倍成本', result.cost_stress?.metrics)
    add('随机基线', result.baselines?.random)
    add('MA20/60', result.baselines?.ma20_60)
    return rows
  }, [result, selected])

  const qualityOption = useMemo<EChartsOption | null>(() => {
    if (!qualityComparison.length) return null
    return {
      animationDuration: 260,
      grid: { left: 52, right: 18, top: 24, bottom: 52 },
      tooltip: { trigger: 'axis', valueFormatter: (value) => Number(value).toFixed(2) },
      xAxis: {
        type: 'category', data: qualityComparison.map((row) => row.label),
        axisLabel: { color: colors.subtext, interval: 0, rotate: qualityComparison.length > 4 ? 18 : 0 },
        axisLine: { lineStyle: { color: colors.axis } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value', name: 'Profit Factor', min: 0, nameTextStyle: { color: colors.subtext },
        axisLabel: { color: colors.subtext }, splitLine: { lineStyle: { color: colors.split } },
      },
      series: [{
        type: 'bar', barMaxWidth: 44,
        data: qualityComparison.map((row) => ({
          value: row.value,
          itemStyle: { color: row.value >= 1 ? colors.up : colors.down, borderRadius: [5, 5, 0, 0] },
        })),
        label: { show: true, position: 'top', color: colors.text, formatter: ({ value }: { value?: unknown }) => Number(value).toFixed(2) },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: colors.warn, type: 'dashed' },
          label: { color: colors.warn, formatter: 'PF = 1' },
          data: [{ yAxis: 1 }],
        },
      }],
    }
  }, [colors, qualityComparison])

  const riskRows = useMemo(() => rankedRows.flatMap((row, index) => {
    const returnValue = portfolioTotalReturn(row.oos)
    const drawdown = portfolioMaxDrawdown(row.oos)
    if (returnValue == null || drawdown == null) return []
    return [{
      name: `组合 ${index + 1}`,
      value: [Math.abs(drawdown) * 100, returnValue * 100, Number(row.oos.net_n_trades || 0)],
      itemStyle: {
        color: returnValue >= 0 ? colors.up : colors.down,
        borderColor: index === 0 ? colors.text : 'transparent',
        borderWidth: index === 0 ? 2 : 0,
      },
    }]
  }), [colors, rankedRows])

  const riskOption = useMemo<EChartsOption | null>(() => {
    if (!riskRows.length) return null
    return {
      animationDuration: 260,
      grid: { left: 62, right: 22, top: 28, bottom: 50 },
      tooltip: {
        trigger: 'item',
        formatter: (raw: unknown) => {
          const item = raw as { name?: string; value?: number[] }
          const value = item.value || []
          return `${item.name || '参数组合'}<br/>OOS 净收益: ${percent(Number(value[1]))}<br/>最大回撤: ${percent(Number(value[0]))}<br/>成交: ${Number(value[2] || 0)} 笔`
        },
      },
      xAxis: {
        type: 'value', name: '最大回撤 (%)', nameLocation: 'middle', nameGap: 32,
        nameTextStyle: { color: colors.subtext }, axisLabel: { color: colors.subtext, formatter: '{value}%' },
        splitLine: { lineStyle: { color: colors.split } },
      },
      yAxis: {
        type: 'value', name: 'OOS 净收益 (%)', nameTextStyle: { color: colors.subtext },
        axisLabel: { color: colors.subtext, formatter: '{value}%' },
        splitLine: { lineStyle: { color: colors.split } },
      },
      series: [{
        type: 'scatter', data: riskRows,
        symbolSize: (value: unknown) => {
          const trades = Array.isArray(value) ? Number(value[2] || 0) : 0
          return Math.min(30, Math.max(9, 8 + Math.sqrt(trades)))
        },
      }],
    }
  }, [colors, riskRows])

  const topRows = useMemo(() => rankedRows.slice(0, 10).flatMap((row, index) => {
    const value = portfolioTotalReturn(row.oos)
    return value == null ? [] : [{ label: `#${index + 1} · ${String(row.signal.box_max_days ?? '?')}日 · ${String(row.signal.breakout_vol_ratio ?? '?')}倍量`, value: value * 100 }]
  }), [rankedRows])

  const topOption = useMemo<EChartsOption | null>(() => {
    if (!topRows.length) return null
    return {
      animationDuration: 260,
      grid: { left: 128, right: 60, top: 18, bottom: 38 },
      tooltip: { trigger: 'axis', valueFormatter: (value) => percent(Number(value)) },
      xAxis: {
        type: 'value', name: 'OOS 净收益 (%)', nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { color: colors.subtext }, axisLabel: { color: colors.subtext, formatter: '{value}%' },
        splitLine: { lineStyle: { color: colors.split } },
      },
      yAxis: {
        type: 'category', inverse: true, data: topRows.map((row) => row.label),
        axisLabel: { color: colors.subtext, width: 116, overflow: 'truncate' },
        axisLine: { lineStyle: { color: colors.axis } }, axisTick: { show: false },
      },
      series: [{
        type: 'bar', barMaxWidth: 20,
        data: topRows.map((row) => ({ value: row.value, itemStyle: { color: row.value >= 0 ? colors.up : colors.down, borderRadius: 3 } })),
        label: { show: true, position: 'right', color: colors.text, formatter: ({ value }: { value?: unknown }) => percent(Number(value)) },
      }],
    }
  }, [colors, topRows])

  const walkForward = useMemo(() => wfRows(result), [result])
  const wfOption = useMemo<EChartsOption | null>(() => {
    if (!walkForward.length) return null
    return {
      animationDuration: 260,
      color: [colors.accent, colors.accent2],
      grid: { left: 54, right: 20, top: 52, bottom: 42 },
      tooltip: { trigger: 'axis' },
      legend: { top: 10, textStyle: { color: colors.subtext } },
      xAxis: {
        type: 'category', data: walkForward.map((row, index) => row.window || `WF${index + 1}`),
        axisLabel: { color: colors.subtext }, axisLine: { lineStyle: { color: colors.axis } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value', name: 'Profit Factor', nameTextStyle: { color: colors.subtext },
        axisLabel: { color: colors.subtext }, splitLine: { lineStyle: { color: colors.split } },
      },
      series: [
        { name: '训练窗 PF', type: 'line', data: walkForward.map((row) => finiteMetric(row.train_pf)), symbolSize: 8, smooth: false },
        { name: '测试窗 PF', type: 'line', data: walkForward.map((row) => finiteMetric(row.test_pf)), symbolSize: 8, smooth: false },
      ],
    }
  }, [colors, walkForward])

  return (
    <section className="result-visuals section-gap" aria-labelledby="result-visuals-title">
      <div className="result-section-heading">
        <div>
          <span className="guide-eyebrow">真实结果可视化</span>
          <h2 id="result-visuals-title">结果图谱</h2>
          <p>全部来自本次持久化回测结果。当前结果不含逐日净值序列，因此不绘制或推测净值曲线。</p>
        </div>
        <span className="pill">评估 {result.evaluated_combinations ?? result.leaderboard.length} 组</span>
      </div>
      <div className="result-chart-grid">
        <ResultChartPanel
          title="净收益对照"
          description="同一入选组合的样本内、样本外、成本压力与基准结果。"
          sample={`${comparison.length} 个可比结果`}
          option={comparisonOption}
          empty="结果未提供可复算的组合净收益。"
        />
        <ResultChartPanel
          title="Profit Factor 对照"
          description="PF 大于 1 表示历史模拟总盈利超过总亏损，仍需与样本数和回撤一起阅读。"
          sample={`${qualityComparison.length} 个可比结果`}
          option={qualityOption}
          empty="结果未提供可复算的净 Profit Factor。"
        />
        <ResultChartPanel
          title="参数风险收益分布"
          description="每个点是一条独立收益路径；横轴越小、纵轴越高越有利，点大小代表成交数。"
          sample={`${riskRows.length} 组有效数据`}
          option={riskOption}
          empty="独立路径排行榜没有同时提供 OOS 组合净收益和最大回撤。"
        />
        <ResultChartPanel
          title="独立路径前十的 OOS 收益"
          description="按精确 IS+OOS 权益路径折叠等效参数，并保持 IS 选参顺序；不按 OOS 重新挑选。"
          sample={`${topRows.length} 组可绘制`}
          option={topOption}
          height={330}
          empty="独立路径排行榜没有可用的 OOS 组合净收益。"
        />
        <ResultChartPanel
          title="WF 窗口稳定性"
          description="逐窗对比训练 PF 与测试 PF；只用于稳定性核验，不用于再次选参。"
          sample={`${walkForward.length} 个窗口`}
          option={wfOption}
          empty="该结果没有返回逐窗口 WF 明细，只能查看汇总指标。"
        />
      </div>
    </section>
  )
}
