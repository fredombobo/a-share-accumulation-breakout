/**
 * 按需注册 ECharts 模块，避免全量 echarts 进主包。
 * treemap 等高级图在 SectorFlow 侧按需扩展时可继续 addChart。
 */
import { useEffect, useRef, useState } from 'react'
import type { EChartsOption } from 'echarts'
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, LineChart, PieChart, ScatterChart, TreemapChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  CandlestickChart,
  ScatterChart,
  TreemapChart,
  GraphicComponent,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
])

export default function EChart({ option, height = 320, ariaLabel }: {
  option: EChartsOption
  height?: number
  ariaLabel?: string
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.EChartsType | null>(null)
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches),
  )

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!media) return
    const onChange = () => setReducedMotion(media.matches)
    onChange()
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    let active = true
    const onResize = () => { if (active) chart.resize() }
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize)
    observer?.observe(ref.current)
    window.addEventListener('resize', onResize)
    return () => {
      active = false
      observer?.disconnect()
      window.removeEventListener('resize', onResize)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    // Returning to normal motion preserves the caller's animation setting.
    const effectiveOption = reducedMotion ? { ...option, animation: false } : option
    chartRef.current?.setOption(effectiveOption, { notMerge: true, lazyUpdate: true })
  }, [option, reducedMotion])

  return <div ref={ref} role={ariaLabel ? 'img' : undefined} aria-label={ariaLabel} style={{ height, width: '100%' }} />
}
