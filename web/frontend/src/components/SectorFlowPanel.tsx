import { useMemo } from 'react'
import { SectorFlowResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'
import type { EChartsOption } from 'echarts'

// 输入单位：万元
const fmt = (v: number) => {
  const abs = Math.abs(v)
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '亿'
  return v.toFixed(1) + '万'
}

/**
 * 板块资金流面板
 * - 左侧：Top 流入/流出板块排行（累计净流入）
 * - 右侧：Top 流入板块的每日资金流趋势（堆叠柱 → 观察建仓节奏）
 */
export default function SectorFlowPanel({ data }: { data: SectorFlowResp }) {
  const c = useChartColors()
  const groupLabel = data.group_label || '板块'
  const groupName = (item: SectorFlowResp['top_in'][number]) => item.group || item.industry

  const trendOption = useMemo<EChartsOption>(() => {
    const top = data.top_in.slice(0, 6).map(groupName)
    const series = top.map((name, i) => ({
      name,
      type: 'bar' as const,
      stack: 'total',
        data: (data.groups || data.industries)[name] || [],
      itemStyle: { color: c.palette[i % c.palette.length] },
      emphasis: { focus: 'series' as const },
    }))
    return {
      backgroundColor: 'transparent',
      legend: { data: top, textStyle: { color: c.text, fontSize: 10 }, type: 'scroll', top: 0 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: any) => fmt(Number(v)),
      },
      grid: { left: 70, right: 20, top: 30, bottom: 44 },
      xAxis: {
        type: 'category' as const,
        data: data.dates,
        axisLabel: { color: c.subtext, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axis } },
      },
      yAxis: {
        type: 'value' as const,
        axisLabel: { color: c.subtext, formatter: (v: number) => fmt(v) },
        splitLine: { lineStyle: { color: c.split } },
      },
      dataZoom: [
        { type: 'inside' as const },
        { type: 'slider' as const, bottom: 4, height: 14, borderColor: c.axis, textStyle: { color: c.subtext } },
      ],
      series,
    }
  }, [data, c])

  return (
    <div className="two-col">
      <div>
        <h3 style={{ margin: '8px 0 6px', fontSize: 13, color: 'var(--muted)' }}>Top 流入{groupLabel}（{data.days}日累计）</h3>
        {data.top_in.slice(0, 6).map((x, i) => (
          <div key={groupName(x)} className="stat">
            <span className="k">{i + 1}. {groupName(x)}</span>
            <span className="v" style={{ color: c.up }}>{fmt(x.net_wan)}</span>
          </div>
        ))}
        <h3 style={{ margin: '12px 0 6px', fontSize: 13, color: 'var(--muted)' }}>Top 流出{groupLabel}（{data.days}日累计）</h3>
        {data.top_out.slice(0, 4).map((x, i) => (
          <div key={groupName(x)} className="stat">
            <span className="k">{i + 1}. {groupName(x)}</span>
            <span className="v" style={{ color: c.down }}>{fmt(x.net_wan)}</span>
          </div>
        ))}
      </div>
      <div>
        <h3 style={{ margin: '8px 0 6px', fontSize: 13, color: 'var(--muted)' }}>Top 流入{groupLabel}每日资金流趋势（堆叠，观察建仓节奏）</h3>
        <EChart option={trendOption} height={260} />
      </div>
    </div>
  )
}
