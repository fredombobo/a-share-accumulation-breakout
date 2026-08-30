import { useMemo } from 'react'
import type { MoneyHeatmapResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'
import type { EChartsOption } from 'echarts'

type HeatmapItem = MoneyHeatmapResp['items'][number]

// 输入单位：万元 → 显示 亿/万
const fmt = (v: number) => {
  const abs = Math.abs(v)
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '亿'
  return v.toFixed(0) + '万'
}

/**
 * 最新交易日资金热力图（treemap）
 * 面积 = |净流入|，颜色 = 方向（A股惯例：流入红 / 流出绿）
 * 参考 nivo treemap 布局风格，用 echarts treemap 实现（零新依赖、主题自动适配）
 */
export default function MoneyHeatmap({ data, height = 380 }: { data: MoneyHeatmapResp; height?: number }) {
  const c = useChartColors()

  const { inflows, outflows } = useMemo(() => ({
    inflows: data.items.filter((item) => item.net_wan > 0).sort((a, b) => b.net_wan - a.net_wan),
    outflows: data.items.filter((item) => item.net_wan < 0).sort((a, b) => a.net_wan - b.net_wan),
  }), [data])

  const makeOption = (items: HeatmapItem[], direction: 'inflow' | 'outflow'): EChartsOption => {
    const maxAbs = Math.max(...items.map((item) => Math.abs(item.net_wan)), 1)
    const tree = items.map((item) => ({
      name: item.name,
      value: item.value,
      itemStyle: {
        color: direction === 'inflow'
          ? `rgba(255, 77, 79, ${0.38 + 0.57 * Math.abs(item.net_wan) / maxAbs})`
          : `rgba(27, 191, 131, ${0.38 + 0.57 * Math.abs(item.net_wan) / maxAbs})`,
      },
      net_wan: item.net_wan,
    }))

    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (p: any) => {
          const d = p.data
          const label = direction === 'inflow' ? '净流入' : '净流出'
          return `<div style="color:#f8fafc"><b style="color:#fff">${d.name}</b><br/>${label}：${fmt(Math.abs(d.net_wan))}<br/>日期：${data.trade_date}</div>`
        },
        textStyle: { color: '#f8fafc' },
        backgroundColor: 'rgba(0,0,0,0.78)',
        borderColor: 'transparent',
      },
      series: [{
        type: 'treemap',
        data: tree,
        width: '100%',
        height: '100%',
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        label: {
          show: true,
          formatter: (p: any) => {
            const d = p.data
            return `${d.name}\n${d.net_wan > 0 ? '+' : ''}${fmt(d.net_wan)}`
          },
          fontSize: 11,
          color: '#fff',
          textShadowColor: 'rgba(0,0,0,0.65)',
          textShadowBlur: 3,
        },
        upperLabel: { show: false },
        itemStyle: {
          borderColor: c.split,
          borderWidth: 2,
          gapWidth: 2,
        },
        emphasis: {
          itemStyle: { borderColor: c.accent, borderWidth: 2 },
        },
      }],
    }
  }

  return (
    <div style={{ position: 'relative' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <h2 style={{ margin: 0, fontSize: 15 }}>{data.classification_title || '细分行业'}资金热力图 <span className="tag">最新交易日</span></h2>
        <div style={{ fontSize: 11, color: 'var(--muted)' }} className="mono">
          数据日 {data.trade_date} · 全市场净流入 {data.total_wan >= 0 ? '+' : ''}{fmt(data.total_wan)}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--muted)', margin: '6px 0 4px' }}>
        <span><i style={{ display: 'inline-block', width: 10, height: 10, background: '#ff4d4f', borderRadius: 2, marginRight: 4 }} />净流入 Top 10（红）</span>
        <span><i style={{ display: 'inline-block', width: 10, height: 10, background: '#1bbf83', borderRadius: 2, marginRight: 4 }} />净流出 Top 10（绿）</span>
        <span>矩形面积 = 净额绝对值</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10 }}>
        <section style={{ minWidth: 0 }}>
          <div style={{ color: 'var(--up-ink)', fontSize: 12, fontWeight: 700, margin: '4px 2px' }}>
            净流入 Top 10（实际 {inflows.length}）
          </div>
          {inflows.length > 0
            ? <EChart option={makeOption(inflows, 'inflow')} height={height} />
            : <div className="muted" style={{ height, display: 'grid', placeItems: 'center' }}>当日无净流入{data.group_label || '分组'}</div>}
        </section>
        <section style={{ minWidth: 0 }}>
          <div style={{ color: 'var(--down-ink)', fontSize: 12, fontWeight: 700, margin: '4px 2px' }}>
            净流出 Top 10（实际 {outflows.length}）
          </div>
          {outflows.length > 0
            ? <EChart option={makeOption(outflows, 'outflow')} height={height} />
            : <div className="muted" style={{ height, display: 'grid', placeItems: 'center' }}>当日无净流出{data.group_label || '分组'}</div>}
        </section>
      </div>
    </div>
  )
}
