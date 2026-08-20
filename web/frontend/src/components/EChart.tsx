/**
 * 按需注册 ECharts 模块，避免全量 echarts 进主包。
 * treemap 等高级图在 SectorFlow 侧按需扩展时可继续 addChart。
 */
import { useEffect, useRef } from 'react'
import type { EChartsOption } from 'echarts'
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, LineChart, PieChart, TreemapChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart,
  BarChart,
  CandlestickChart,
  PieChart,
  TreemapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  MarkPointComponent,
  VisualMapComponent,
  CanvasRenderer,
])

export default function EChart({ option, height = 320 }: { option: EChartsOption; height?: number }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.EChartsType | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true, lazyUpdate: true })
  }, [option])

  return <div ref={ref} style={{ height, width: '100%' }} />
}
