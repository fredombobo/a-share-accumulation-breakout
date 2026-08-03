import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router'
import { api, StockDetail as Detail, StockFlowResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import FundFlowChart from '../components/FundFlowChart'
import type { EChartsOption } from 'echarts'

const num = (x: number | null | undefined, d = 2) => (x == null || isNaN(x as number) ? 'n/a' : (x as number).toFixed(d))
// 输入单位：万元
const fmtWan = (v: number) => {
  const abs = Math.abs(v)
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + ' 亿'
  if (abs >= 1e2) return v.toFixed(1) + ' 万'
  return v.toFixed(0) + ' 万'
}

export default function StockDetail() {
  const { tsCode = '' } = useParams()
  const [s, setS] = useState<Detail | null>(null)
  const [flowData, setFlowData] = useState<StockFlowResp | null>(null)
  const [flowDays, setFlowDays] = useState(20)
  const [err, setErr] = useState('')
  const nav = useNavigate()
  const c = useChartColors()

  useEffect(() => {
    setS(null)
    setFlowData(null)
    setErr('')
    const code = decodeURIComponent(tsCode)
    api.stock(code)
      .then(setS)
      .catch((e) => setErr(String(e)))
    api.stockFlow(code, flowDays)
      .then(setFlowData)
      .catch(() => setFlowData(null))
  }, [tsCode, flowDays])

  const klineOpt = useMemo<EChartsOption | null>(() => {
    if (!s) return null
    const ohlc = s.kline || []
    const cat = ohlc.map((d) => d.trade_date)
    const candle = ohlc.map((d) => [d.open, d.close, d.low, d.high])
    const vol = ohlc.map((d) => d.vol)
    const ma5 = ohlc.map((_, i) => {
      if (i < 4) return null
      let sum = 0
      for (let j = i - 4; j <= i; j++) sum += ohlc[j].close
      return +(sum / 5).toFixed(2)
    })
    const ma10 = ohlc.map((_, i) => {
      if (i < 9) return null
      let sum = 0
      for (let j = i - 9; j <= i; j++) sum += ohlc[j].close
      return +(sum / 10).toFixed(2)
    })
    const ma20 = ohlc.map((_, i) => {
      if (i < 19) return null
      let sum = 0
      for (let j = i - 19; j <= i; j++) sum += ohlc[j].close
      return +(sum / 20).toFixed(2)
    })
    const bh = s.signal.box_high
    const bl = s.signal.box_low
    const bd = s.signal.breakout_date
    const markLineData: any[] = []
    if (bh != null) markLineData.push({ yAxis: bh, name: '箱体上沿' })
    if (bl != null) markLineData.push({ yAxis: bl, name: '箱体下沿' })
    // 突破日格式统一：后端 breakout_date 为 'YYYY-MM-DD'，xAxis 类别为 trade_date 'YYYYMMDD'
    const bdNorm = bd ? bd.replace(/-/g, '') : null
    const bdKline = bdNorm ? s.kline.find((d) => d.trade_date === bdNorm) : null
    const markPointData: any[] = []
    if (bdKline) markPointData.push({ coord: [bdNorm!, bdKline.high], value: '突破', symbolSize: 60 })

    return {
      backgroundColor: 'transparent',
      legend: { data: ['K线', 'MA5', 'MA10', 'MA20', '箱体上沿', '箱体下沿'], textStyle: { color: c.text }, top: 0 },
      tooltip: { trigger: 'axis' as const, axisPointer: { type: 'cross' as const } },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 60, right: 24, top: 40, height: '56%' },
        { left: 60, right: 24, top: '72%', height: '16%' },
      ],
      xAxis: [
        { type: 'category' as const, data: cat, axisLabel: { color: c.subtext }, axisLine: { lineStyle: { color: c.axis } } },
        { type: 'category' as const, gridIndex: 1, data: cat, axisLabel: { color: c.subtext }, axisLine: { lineStyle: { color: c.axis } } },
      ],
      yAxis: [
        { scale: true, axisLabel: { color: c.subtext }, splitLine: { lineStyle: { color: c.split } } },
        { scale: true, gridIndex: 1, axisLabel: { color: c.subtext, formatter: (v: number) => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'K' : String(v) }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside' as const, xAxisIndex: [0, 1], start: 30, end: 100 },
        { type: 'slider' as const, xAxisIndex: [0, 1], bottom: 2, height: 16, borderColor: c.axis, textStyle: { color: c.subtext } },
      ],
      series: [
        {
          name: 'K线', type: 'candlestick' as const, data: candle,
          itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
          markLine: markLineData.length
            ? {
                symbol: 'none',
                label: { color: c.warn, fontSize: 10, formatter: '{b}' },
                lineStyle: { type: 'dashed' as const, width: 1 },
                data: markLineData,
              }
            : undefined,
          markPoint: markPointData.length
            ? {
                symbol: 'pin',
                symbolSize: 46,
                label: { color: '#fff', fontSize: 10, formatter: '突' },
                itemStyle: { color: c.warn },
                data: markPointData,
              }
            : undefined,
        },
        { name: 'MA5', type: 'line' as const, data: ma5, smooth: true, symbol: 'none', lineStyle: { width: 1.2, color: c.accent }, connectNulls: true },
        { name: 'MA10', type: 'line' as const, data: ma10, smooth: true, symbol: 'none', lineStyle: { width: 1.2, color: c.warn }, connectNulls: true },
        { name: 'MA20', type: 'line' as const, data: ma20, smooth: true, symbol: 'none', lineStyle: { width: 1.2, color: c.accent2 }, connectNulls: true },
        {
          name: '成交量', type: 'bar' as const, xAxisIndex: 1, yAxisIndex: 1, data: vol,
          itemStyle: { color: (p: any) => { const d = ohlc[p.dataIndex]; return d ? (d.close >= d.open ? c.up : c.down) : c.up } },
        },
      ],
    }
  }, [s, c])

  if (err) return <div className="err">加载失败：{err}</div>
  if (!s) return <div className="loading">加载中…</div>

  const f = s.fundamentals
  const sig = s.signal
  const flow = s.fund_flow

  return (
    <div>
      <div className="row section-gap" style={{ marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>
            {s.name} <span className="muted mono">{s.ts_code}</span>
          </h2>
          <div className="muted">
            {s.industry} · 现价 {num(f.close)} · PE {num(f.pe)} · PB {num(f.pb)} · 市值 {f.total_mv_wan ? (f.total_mv_wan / 1e4).toFixed(0) : 'n/a'}亿
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn" onClick={() => nav('/')}>← 返回总览</button>
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h2>K线 <span className="tag">近120日 · 箱体虚线 + 突破标记 + 量能</span></h2>
          {klineOpt && <EChart option={klineOpt} height={460} />}
        </div>
        <div className="card">
          <h2>信号解读 <span className="tag">横盘吸筹 → 启动</span></h2>
          <div className="stat"><span className="k">箱体天数</span><span className="v">{sig.box_days ?? 'n/a'} 日</span></div>
          <div className="stat"><span className="k">箱体振幅</span><span className="v">{sig.box_amp != null ? (sig.box_amp * 100).toFixed(1) + '%' : 'n/a'}</span></div>
          <div className="stat"><span className="k">箱体区间</span><span className="v mono">{num(sig.box_low)} ~ {num(sig.box_high)}</span></div>
          <div className="stat"><span className="k">突破日</span><span className="v">{sig.breakout_date ?? 'n/a'}</span></div>
          <div className="stat"><span className="k">突破量比</span><span className="v">{sig.breakout_vol_ratio != null ? sig.breakout_vol_ratio.toFixed(1) + 'x' : 'n/a'}</span></div>
          <div className="stat"><span className="k">突破日涨幅</span><span className="v" style={{ color: (sig.breakout_pct_chg ?? 0) >= 0 ? c.up : c.down }}>{sig.breakout_pct_chg != null ? (sig.breakout_pct_chg * 100).toFixed(2) + '%' : 'n/a'}</span></div>
          <div className="stat"><span className="k">缩量系数</span><span className="v">{sig.vol_shrink_ratio != null ? sig.vol_shrink_ratio.toFixed(2) : 'n/a'}</span></div>
          <div className="stat"><span className="k">MA5 / MA10 / MA20</span><span className="v mono">{num(sig.ma5)} / {num(sig.ma10)} / {num(sig.ma20)}</span></div>
          <div className="note">{sig.reasons?.join('；') || '—'}</div>
        </div>
      </div>

      <div className="card section-gap">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
          <h2 style={{ margin: 0 }}>
            💰 资金流趋势 <span className="tag">个股 vs {s.industry} 板块 · 观察建仓/出逃</span>
          </h2>
          <div className="row">
            <span className="muted" style={{ fontSize: 12 }}>周期</span>
            {[5, 10, 20].map((n) => (
              <button
                key={n}
                className="btn"
                style={{ padding: '4px 10px', borderColor: flowDays === n ? 'var(--accent)' : 'var(--border)', color: flowDays === n ? 'var(--accent)' : 'var(--text)' }}
                onClick={() => setFlowDays(n)}
              >
                {n}日
              </button>
            ))}
          </div>
        </div>
        {flowData && flowData.stock_flow.length > 0 ? (
          <FundFlowChart
            dates={flowData.stock_flow.map((r) => r.trade_date.slice(4))}
            stockNet={flowData.stock_flow.map((r) => r.net_wan * 10000)}
            stockBuy={flowData.stock_flow.map((r) => r.buy_main_wan * 10000)}
            stockSell={flowData.stock_flow.map((r) => r.sell_main_wan * 10000)}
            sectorDates={(flowData.sector_flow.dates || []).map((d) => d.slice(4))}
            sectorNet={(flowData.sector_flow.net_wan || []).map((v) => v * 10000)}
            height={320}
          />
        ) : (
          <div className="muted">资金流数据加载中…</div>
        )}
        {flowData && (
          <div className="note">
            红柱=主力净流入，绿柱=净流出；虚线=主力买入金额（超大单+大单）；淡蓝/淡红柱=所在{s.industry}板块净流入。
            {flowData.stock_flow.length >= 5 && (
              <>
                近5日个股累计：<b style={{ color: flowData.stock_flow.slice(-5).reduce((s, r) => s + r.net_wan, 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {fmtWan(flowData.stock_flow.slice(-5).reduce((s, r) => s + r.net_wan, 0))}
                </b>
              </>
            )}
          </div>
        )}
      </div>

      <div className="two-col section-gap">
        <div className="card">
          <h2>资金流 <span className="tag">近5日主力</span></h2>
          <div className="stat"><span className="k">主力净流入</span><span className="v" style={{ color: s.fund_flow.net_wan >= 0 ? c.up : c.down }}>{s.fund_flow.net_wan >= 1e4 ? (s.fund_flow.net_wan / 1e4).toFixed(2) + ' 亿' : s.fund_flow.net_wan.toFixed(0) + ' 万'}</span></div>
          <div className="stat"><span className="k">净流入 / 成交额</span><span className="v">{s.fund_flow.ratio_pct.toFixed(2)}%</span></div>
          <div className="stat"><span className="k">资金强度分</span><span className="v">{s.fund_flow.score.toFixed(1)} / 100</span></div>
          <div className="note">主力 = 超大单 + 大单；净流入为 5 日累计</div>
        </div>
        <div className="card">
          <h2>财务摘要 <span className="tag">最新财报 {s.fina && s.fina[0] ? s.fina[0].end_date : 'n/a'}</span></h2>
          <div className="stat"><span className="k">PE(TTM)</span><span className="v">{num(f.pe)}</span></div>
          <div className="stat"><span className="k">PB</span><span className="v">{num(f.pb)}</span></div>
          <div className="stat"><span className="k">总市值</span><span className="v">{f.total_mv_wan ? (f.total_mv_wan / 1e4).toFixed(0) + ' 亿' : 'n/a'}</span></div>
          <div className="stat"><span className="k">流通市值</span><span className="v">{f.circ_mv_wan ? (f.circ_mv_wan / 1e4).toFixed(0) + ' 亿' : 'n/a'}</span></div>
          <div className="stat"><span className="k">换手率</span><span className="v">{f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : 'n/a'}</span></div>
          <div className="stat"><span className="k">量比</span><span className="v">{f.volume_ratio != null ? f.volume_ratio.toFixed(2) : 'n/a'}</span></div>
          {s.fina && s.fina[0] && (
            <>
              <div style={{ borderTop: '1px dashed var(--border)', margin: '10px 0 6px' }} />
              <div className="stat"><span className="k">ROE(加权)</span><span className="v">{s.fina[0].roe_waa != null ? s.fina[0].roe_waa.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">ROA</span><span className="v">{s.fina[0].roa != null ? s.fina[0].roa.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">毛利率</span><span className="v">{s.fina[0].grossprofit_margin != null ? s.fina[0].grossprofit_margin.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">净利率</span><span className="v">{s.fina[0].netprofit_margin != null ? s.fina[0].netprofit_margin.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">营收增速</span><span className="v" style={{ color: (s.fina[0].or_yoy ?? 0) >= 0 ? c.up : c.down }}>{s.fina[0].or_yoy != null ? s.fina[0].or_yoy.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">净利增速</span><span className="v" style={{ color: (s.fina[0].netprofit_yoy ?? 0) >= 0 ? c.up : c.down }}>{s.fina[0].netprofit_yoy != null ? s.fina[0].netprofit_yoy.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">资产负债率</span><span className="v">{s.fina[0].debt_to_assets != null ? s.fina[0].debt_to_assets.toFixed(2) + '%' : 'n/a'}</span></div>
              <div className="stat"><span className="k">经营现金流/营收</span><span className="v">{s.fina[0].ocf_to_or != null ? s.fina[0].ocf_to_or.toFixed(2) : 'n/a'}</span></div>
              <div className="stat"><span className="k">EPS</span><span className="v">{num(s.fina[0].eps)}</span></div>
            </>
          )}
          <div className="note">数据截至 {s.as_of}</div>
        </div>
      </div>
    </div>
  )
}
