import { useEffect, useMemo, useRef, useState } from 'react'
import { api, BacktestTrade, KlinePoint } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from './EChart'
import { IcoScan, IcoStop } from './Icons'
import type { EChartsOption } from 'echarts'

const norm = (d: string) => d.replace(/-/g, '').slice(0, 8)
const fmtDay = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`

const EXIT_LABEL: Record<string, string> = { stop: '止损', bench: '标杆量出货', target: '止盈', time: '到期强平' }

/** 单笔交易 K 线：箱体虚线 + 突破/买入/卖出标注 + 分步播放。 */
export default function TradeChart({ trade }: { trade: BacktestTrade }) {
  const c = useChartColors()
  const [kline, setKline] = useState<KlinePoint[] | null>(null)
  const [err, setErr] = useState('')
  const [step, setStep] = useState<number | null>(null) // null=全部
  const [playing, setPlaying] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    setKline(null)
    setErr('')
    setStep(null)
    const toDate = (d: string) => {
      const n = norm(d)
      if (n.length !== 8) return null
      return new Date(`${n.slice(0, 4)}-${n.slice(4, 6)}-${n.slice(6, 8)}T00:00:00`)
    }
    const start = toDate(trade.signal_date) || new Date()
    start.setDate(start.getDate() - 100)
    const end = toDate(trade.exit_date) || toDate(trade.signal_date) || new Date()
    end.setDate(end.getDate() + 15)
    const fmt = (d: Date) => d.toISOString().slice(0, 10).replace(/-/g, '')
    api.kline(trade.ts_code, fmt(start), fmt(end))
      .then((r) => {
        setKline(r.kline)
        setStep(r.kline.length)
      })
      .catch((e: unknown) => setErr(String(e)))
  }, [trade])

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current) }, [])

  const shown = useMemo(() => {
    if (!kline) return []
    return step === null ? kline : kline.slice(0, step)
  }, [kline, step])

  const onPlay = () => {
    if (!kline) return
    if (playing) {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
      setPlaying(false)
      setStep(kline.length)
      return
    }
    setStep(0)
    setPlaying(true)
    timerRef.current = setInterval(() => {
      setStep((prev) => {
        const next = (prev ?? 0) + 3
        if (kline && next >= kline.length) {
          if (timerRef.current) clearInterval(timerRef.current)
          timerRef.current = null
          setPlaying(false)
          return kline.length
        }
        return next
      })
    }, 110)
  }

  const option = useMemo<EChartsOption | null>(() => {
    if (!shown.length) return null
    const cat = shown.map((d) => d.trade_date)
    const candle = shown.map((d) => [d.open, d.close, d.low, d.high])
    const ma = (n: number) =>
      shown.map((_, i) => {
        if (i < n - 1) return null
        let sum = 0
        for (let j = i - n + 1; j <= i; j++) sum += shown[j].close
        return +(sum / n).toFixed(2)
      })
    const markPointData: any[] = []
    const bd = norm(trade.breakout_date || '')
    if (bd) {
      const bar = shown.find((d) => d.trade_date === bd)
      if (bar) markPointData.push({ coord: [bd, bar.high], value: '突破', symbol: 'pin', symbolSize: 40, itemStyle: { color: c.warn }, label: { color: '#fff', fontSize: 10, formatter: '突' } })
    }
    const ed = norm(trade.entry_date || '')
    if (ed && trade.entry_price != null && shown.some((d) => d.trade_date === ed)) {
      markPointData.push({ coord: [ed, trade.entry_price], value: '买入', symbol: 'circle', symbolSize: 10, itemStyle: { color: c.accent, borderColor: '#fff', borderWidth: 1 }, label: { show: true, position: 'bottom', color: c.accent, fontSize: 10, formatter: '买' } })
    }
    const xd = norm(trade.exit_date || '')
    if (xd && trade.exit_price != null && shown.some((d) => d.trade_date === xd)) {
      const isStop = trade.exit === 'stop'
      markPointData.push({ coord: [xd, trade.exit_price], value: '卖出', symbol: 'circle', symbolSize: 10, itemStyle: { color: isStop ? c.up : c.down, borderColor: '#fff', borderWidth: 1 }, label: { show: true, position: 'top', color: isStop ? c.up : c.down, fontSize: 10, formatter: '卖' } })
    }
    const markLineData: any[] = []
    if (trade.box_high != null) markLineData.push({ yAxis: trade.box_high, name: '箱体上沿' })
    if (trade.box_low != null) markLineData.push({ yAxis: trade.box_low, name: '箱体下沿' })

    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 10, right: 12, top: 26, bottom: 4, containLabel: true },
      tooltip: { trigger: 'axis' as const, confine: true },
      xAxis: { type: 'category' as const, data: cat, axisLabel: { color: c.subtext, fontSize: 10, formatter: (v: string) => v.slice(4).replace(/(\d{2})(\d{2})/, '$1/$2') }, axisLine: { lineStyle: { color: c.axis } } },
      yAxis: { type: 'value' as const, scale: true, splitLine: { lineStyle: { color: c.split } }, axisLabel: { color: c.subtext, fontSize: 10 } },
      series: [
        {
          name: 'K线', type: 'candlestick' as const, data: candle,
          itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
          markLine: markLineData.length ? { symbol: 'none', lineStyle: { type: 'dashed' as const, width: 1 }, label: { color: c.warn, fontSize: 9, formatter: '{b}' }, data: markLineData } : undefined,
          markPoint: markPointData.length ? { data: markPointData } : undefined,
        },
        { name: 'MA5', type: 'line' as const, data: ma(5), smooth: true, symbol: 'none', lineStyle: { width: 1, color: c.accent } },
        { name: 'MA10', type: 'line' as const, data: ma(10), smooth: true, symbol: 'none', lineStyle: { width: 1, color: c.warn } },
      ],
    }
  }, [shown, trade, c])

  return (
    <div className="trade-chart">
      <div className="trade-chart-head">
        <div>
          <span className="mono" style={{ fontSize: 13, fontWeight: 700 }}>{trade.ts_code}</span>
          <span className="badge badge-mute" style={{ marginLeft: 8 }}>采样 {fmtDay(norm(trade.signal_date))}</span>
          {trade.breakout_date && <span className="badge badge-warn" style={{ marginLeft: 6 }}>突破 {fmtDay(norm(trade.breakout_date))}</span>}
          <span className="badge badge-accent" style={{ marginLeft: 6 }}>{EXIT_LABEL[trade.exit] || trade.exit || '未成交'}</span>
        </div>
        <button className="btn btn-sm" onClick={onPlay} disabled={!kline?.length}>
          {playing ? <IcoStop size={13} /> : <IcoScan size={13} />}
          {playing ? '停止' : '▶ 播放'}
        </button>
      </div>
      {err && <div className="err-inline">{err}</div>}
      {!kline && !err && <div className="muted" style={{ fontSize: 12, padding: '20px 0', textAlign: 'center' }}>加载 K 线…</div>}
      {kline && kline.length === 0 && <div className="muted" style={{ fontSize: 12 }}>该区间无 K 线数据</div>}
      {kline && kline.length > 0 && option && <EChart option={option} height={260} />}
      <div className="trade-chart-stats">
        <div><span>买入日</span><b className="num">{trade.entry_date ? fmtDay(norm(trade.entry_date)) : '—'}</b></div>
        <div><span>买入价</span><b className="num">{trade.entry_price != null ? trade.entry_price.toFixed(2) : '—'}</b></div>
        <div><span>卖出日</span><b className="num">{trade.exit_date ? fmtDay(norm(trade.exit_date)) : '—'}</b></div>
        <div><span>卖出价</span><b className="num">{trade.exit_price != null ? trade.exit_price.toFixed(2) : '—'}</b></div>
        <div><span>持有</span><b className="num">{trade.days ?? '—'} 日</b></div>
        <div><span>净收益</span>
          <b className="num" style={{ color: (trade.net_return ?? 0) >= 0 ? 'var(--ok-ink)' : 'var(--up-ink)' }}>
            {trade.net_return != null ? `${(trade.net_return * 100).toFixed(2)}%` : '—'}
          </b>
        </div>
      </div>
    </div>
  )
}
