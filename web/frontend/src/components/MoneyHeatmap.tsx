import { useMemo } from 'react'
import { MoneyHeatmapResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'
import type { EChartsOption } from 'echarts'

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

  const option = useMemo<EChartsOption>(() => {
    const tree = data.items.map((it) => ({
      name: it.name,
      value: it.value,
      // 流入红（up）/ 流出绿（down），颜色随净额比例渐变透明度
      itemStyle: {
        color: it.net_wan >= 0
          ? `rgba(255, 77, 79, ${0.35 + 0.6 * Math.min(1, it.net_wan / (data.total_wan > 0 ? Math.max(data.total_wan, 1e6) : 1e6))})`
          : `rgba(27, 191, 131, ${0.35 + 0.6 * Math.min(1, Math.abs(it.net_wan) / 1e6)})`,
      },
      net_wan: it.net_wan,
    }))

    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (p: any) => {
          const d = p.data
          const sign = d.net_wan >= 0 ? '+' : ''
          return `<b>${d.name}</b><br/>净流入：${sign}${fmt(d.net_wan)}<br/>日期：${data.trade_date}`
        },
        textStyle: { color: c.text },
        backgroundColor: 'rgba(0,0,0,0.75)',
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
            const sign = d.net_wan >= 0 ? '+' : ''
            return `${d.name}\n${sign}${fmt(d.net_wan)}`
          },
          fontSize: 11,
          color: '#fff',
          textShadowColor: 'rgba(0,0,0,0.6)',
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
  }, [data, c])

  return (
    <div style={{ position: 'relative' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <h2 style={{ margin: 0, fontSize: 15 }}>💰 资金热力图 <span className="tag">最新交易日</span></h2>
        <div style={{ fontSize: 11, color: 'var(--muted)' }} className="mono">
          数据日 {data.trade_date} · 全市场净流入 {data.total_wan >= 0 ? '+' : ''}{fmt(data.total_wan)}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--muted)', margin: '6px 0 4px' }}>
        <span><i style={{ display: 'inline-block', width: 10, height: 10, background: '#ff4d4f', borderRadius: 2, marginRight: 4 }} />净流入（红）</span>
        <span><i style={{ display: 'inline-block', width: 10, height: 10, background: '#1bbf83', borderRadius: 2, marginRight: 4 }} />净流出（绿）</span>
        <span>矩形面积 = 净额绝对值</span>
      </div>
      <EChart option={option} height={height} />
    </div>
  )
}
