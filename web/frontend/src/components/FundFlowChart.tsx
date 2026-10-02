import { useMemo } from 'react'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'
import type { EChartsOption } from 'echarts'

/**
 * 资金流趋势图
 * - 主图：个股每日主力净流入柱状图（红正绿负，0 轴上下）
 * - 副图：所在板块每日净流入（折线/柱，双轴对比）
 * 可观察主力建仓（连续净流入）或出逃（连续净流出）时段。
 */
export default function FundFlowChart({
  dates,
  stockNet,
  stockBuy,
  stockSell,
  sectorDates,
  sectorNet,
  height = 300,
}: {
  dates: string[]
  stockNet: (number | null)[]
  stockBuy: (number | null)[]
  stockSell: (number | null)[]
  sectorDates: string[]
  sectorNet: (number | null)[]
  height?: number
}) {
  const c = useChartColors()

  const fmt = (v: number) => {
    const abs = Math.abs(v)
    if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
    if (abs >= 1e4) return (v / 1e4).toFixed(1) + '万'
    return String(Math.round(v))
  }

  // ── 建仓/出逃段自动识别：连续 N 日同向净流入=建仓、净流出=出逃 ──
  const MIN_RUN = 3
  const segments = useMemo(() => {
    const segs: { start: number; end: number; type: 'accumulate' | 'distribute' }[] = []
    if (stockNet.length < MIN_RUN) return segs
    let i = 0
    while (i < stockNet.length) {
      const initial = stockNet[i]
      if (initial == null || initial === 0) { i++; continue }
      let j = i
      while (j + 1 < stockNet.length && stockNet[j + 1] != null && Math.sign(stockNet[j + 1]!) === Math.sign(initial)) j++
      const runLen = j - i + 1
      if (runLen >= MIN_RUN) {
        segs.push({
          start: i,
          end: j,
          type: initial > 0 ? 'accumulate' : 'distribute',
        })
      }
      i = j + 1
    }
    return segs
  }, [stockNet])

  const accumulateSegs = segments.filter((s) => s.type === 'accumulate')
  const distributeSegs = segments.filter((s) => s.type === 'distribute')

  const markAreaData: any[] = []
  accumulateSegs.forEach((s) => {
    markAreaData.push([
      { name: '连续净流入', xAxis: dates[s.start], itemStyle: { color: 'rgba(59,130,246,0.08)' } },
      { xAxis: dates[s.end] },
    ])
  })
  distributeSegs.forEach((s) => {
    markAreaData.push([
      { name: '连续净流出', xAxis: dates[s.start], itemStyle: { color: 'rgba(244,63,94,0.08)' } },
      { xAxis: dates[s.end] },
    ])
  })

  const option: EChartsOption = {
    backgroundColor: 'transparent',
    legend: { data: ['个股资金净流入', '大单及特大单买入', '板块净流入'], textStyle: { color: c.text }, top: 0 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: any) => v == null ? '缺少数据' : fmt(Number(v)),
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 70, right: 70, top: 36, height: '38%' },
      { left: 70, right: 70, top: '58%', height: '30%' },
    ],
    xAxis: [
      { type: 'category', data: dates, axisLabel: { color: c.subtext, fontSize: 10 }, axisLine: { lineStyle: { color: c.axis } } },
      { type: 'category', gridIndex: 1, data: sectorDates, axisLabel: { color: c.subtext, fontSize: 10 }, axisLine: { lineStyle: { color: c.axis } } },
    ],
    yAxis: [
      {
        type: 'value',
        axisLabel: { color: c.subtext, formatter: (v: number) => fmt(v) },
        splitLine: { lineStyle: { color: c.split } },
      },
      {
        type: 'value', gridIndex: 1,
        axisLabel: { color: c.subtext, formatter: (v: number) => fmt(v) },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 14, borderColor: c.axis, textStyle: { color: c.subtext } },
    ],
    series: [
      {
        name: '个股资金净流入',
        type: 'bar',
        data: stockNet,
        barWidth: '55%',
        itemStyle: {
          color: (p: any) => (p.value >= 0 ? c.up : c.down),
        },
        markArea: markAreaData.length
          ? {
              silent: true,
              itemStyle: { color: 'transparent' },
              label: { show: true, color: c.subtext, fontSize: 10 },
              data: markAreaData,
            }
          : undefined,
      },
      {
        name: '大单及特大单买入',
        type: 'line',
        data: stockBuy,
        symbol: 'none',
        lineStyle: { width: 1.2, color: c.accent, type: 'dashed' },
        itemStyle: { color: c.accent },
        connectNulls: false,
        z: 3,
      },
      {
        name: '板块净流入',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: sectorNet,
        barWidth: '40%',
        itemStyle: {
          color: (p: any) => {
            const v = p.value as number
            return v >= 0 ? 'rgba(59,130,246,0.35)' : 'rgba(244,63,94,0.35)'
          },
        },
      },
    ],
  }

  return <EChart option={option} height={height} />
}
