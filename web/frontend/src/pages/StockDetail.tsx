import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router'
import { api, StockDetail as Detail, StockFlowResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import FundFlowChart from '../components/FundFlowChart'
import AIReviewPanel from '../components/AIReviewPanel'
import { IcoArrowRight, IcoTarget } from '../components/Icons'
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
    const ma = (n: number) =>
      ohlc.map((_, i) => {
        if (i < n - 1) return null
        let sum = 0
        for (let j = i - n + 1; j <= i; j++) sum += ohlc[j].close
        return +(sum / n).toFixed(2)
      })
    const ma5 = ma(5)
    const ma10 = ma(10)
    const ma20 = ma(20)
    const bh = s.signal.box_high
    const bl = s.signal.box_low
    const bd = s.signal.breakout_date
    const markLineData: any[] = []
    if (bh != null) markLineData.push({ yAxis: bh, name: '箱体上沿' })
    if (bl != null) markLineData.push({ yAxis: bl, name: '箱体下沿' })
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
  const priceUp = (f.close ?? 0) >= (s.kline?.length >= 2 ? s.kline[s.kline.length - 2].close : f.close ?? 0)

  return (
    <div className="fade-up">
      {/* 头部英雄条 */}
      <div className="stock-hero">
        <div className="stock-hero-main">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <span className="stock-hero-name">{s.name}</span>
            <span className="mono" style={{ color: 'var(--muted)', fontSize: 14 }}>{s.ts_code}</span>
            <span className="pill accent">{s.industry}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginTop: 8, flexWrap: 'wrap' }}>
            <span className="stock-hero-price num" style={{ color: priceUp ? 'var(--up)' : 'var(--down)' }}>
              {num(f.close)}
            </span>
            <span className="muted" style={{ fontSize: 13 }}>
              PE <b className="num">{num(f.pe)}</b> · PB <b className="num">{num(f.pb)}</b> · 市值{' '}
              <b className="num">{f.total_mv_wan ? (f.total_mv_wan / 1e4).toFixed(0) : 'n/a'}亿</b> · 换手{' '}
              <b className="num">{f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : 'n/a'}</b>
            </span>
          </div>
          <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>数据截至 {s.as_of}</div>
        </div>
        <div className="stock-hero-actions">
          <button className="btn primary" onClick={() => document.getElementById('ai-review')?.scrollIntoView({ behavior: 'smooth' })}>
            AI 证据评测
          </button>
          <button className="btn" onClick={() => nav('/')}>
            返回总览 <IcoArrowRight size={13} style={{ transform: 'rotate(180deg)' }} />
          </button>
        </div>
      </div>

      <AIReviewPanel tsCode={s.ts_code} />

      <div className="two-col section-gap" style={{ gridTemplateColumns: 'minmax(0, 1.6fr) minmax(0, 1fr)' }}>
        {/* 左：K 线 */}
        <div className="card">
          <div className="h-sec">
            <h2 style={{ margin: 0 }}>K 线 <span className="tag">箱体虚线 + 突破标记 + 量能</span></h2>
          </div>
          {klineOpt && <EChart option={klineOpt} height={460} />}
        </div>

        {/* 右：信号 + 资金 + 交易卡 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <div className="card">
            <div className="h-sec" style={{ marginBottom: 10 }}>
              <h2 style={{ margin: 0 }}>信号解读</h2>
              {sig.breakout_date && <span className="pill warn">突破日 {sig.breakout_date}</span>}
            </div>
            <div className="sig-grid">
              <div><span>箱体天数</span><b className="num">{sig.box_days ?? 'n/a'} 日</b></div>
              <div><span>箱体振幅</span><b className="num">{sig.box_amp != null ? (sig.box_amp * 100).toFixed(1) + '%' : 'n/a'}</b></div>
              <div><span>箱体区间</span><b className="mono num">{num(sig.box_low)} ~ {num(sig.box_high)}</b></div>
              <div><span>突破量比</span><b className="num">{sig.breakout_vol_ratio != null ? sig.breakout_vol_ratio.toFixed(1) + 'x' : 'n/a'}</b></div>
              <div><span>突破日涨幅</span>
                <b className="num" style={{ color: (sig.breakout_pct_chg ?? 0) >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {sig.breakout_pct_chg != null ? (sig.breakout_pct_chg * 100).toFixed(2) + '%' : 'n/a'}
                </b></div>
              <div><span>缩量系数</span><b className="num">{sig.vol_shrink_ratio != null ? sig.vol_shrink_ratio.toFixed(2) : 'n/a'}</b></div>
              <div><span>MA5 / 10 / 20</span><b className="mono num">{num(sig.ma5)} / {num(sig.ma10)} / {num(sig.ma20)}</b></div>
            </div>
            <div className="note" style={{ marginTop: 12 }}>{sig.reasons?.join('；') || '—'}</div>
          </div>

          <div className="card">
            <div className="h-sec" style={{ marginBottom: 10 }}>
              <h2 style={{ margin: 0 }}>近 5 日资金</h2>
              <span className="pill" style={{ gap: 5 }}><IcoTarget size={12} />主力 = 超大单 + 大单</span>
            </div>
            <div className="sig-grid">
              <div><span>主力净流入</span>
                <b className="num" style={{ color: s.fund_flow.net_wan >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {s.fund_flow.net_wan >= 1e4 ? (s.fund_flow.net_wan / 1e4).toFixed(2) + ' 亿' : s.fund_flow.net_wan.toFixed(0) + ' 万'}
                </b></div>
              <div><span>净流入 / 成交额</span><b className="num">{s.fund_flow.ratio_pct.toFixed(2)}%</b></div>
              <div><span>资金强度分</span><b className="num">{s.fund_flow.score.toFixed(1)} / 100</b></div>
            </div>
          </div>

          {s.trade && (
            <div className="card" style={{ borderColor: 'color-mix(in srgb, var(--accent) 40%, var(--border))' }}>
              <div className="h-sec" style={{ marginBottom: 10 }}>
                <h2 style={{ margin: 0 }}>交易卡片</h2>
                <span className={`pill ${s.trade.tradeable ? 'ok' : 'warn'}`}>{s.trade.tradeable ? '严格研究候选' : '观察'}</span>
              </div>
              <div className="sig-grid">
                <div><span>入场参考</span><b className="num">{s.trade.entry_ref ?? '—'}</b></div>
                <div><span>止损</span><b className="num text-danger">{s.trade.stop_loss ?? '—'}</b></div>
                <div><span>目标 1</span><b className="num text-ok">{s.trade.target_1 ?? '—'}</b></div>
                <div><span>目标 2</span><b className="num text-ok">{s.trade.target_2 ?? '—'}</b></div>
                <div><span>建议仓位</span><b className="num">{s.trade.position_pct}%</b></div>
                <div><span>最长持有</span><b className="num">{s.trade.max_hold_days} 日</b></div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 资金流趋势 */}
      <div className="card section-gap">
        <div className="h-sec">
          <h2 style={{ margin: 0 }}>资金流趋势 <span className="tag">个股 vs {s.industry} 板块</span></h2>
          <div className="seg">
            {[5, 10, 20].map((n) => (
              <button key={n} className={`seg-item ${flowDays === n ? 'on' : ''}`} onClick={() => setFlowDays(n)}>{n} 日</button>
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
            红柱=主力净流入，绿柱=净流出；虚线=主力买入金额；淡蓝/淡红柱=所在{s.industry}板块净流入。
            {flowData.stock_flow.length >= 5 && (
              <>
                近5日个股累计：
                <b style={{ color: flowData.stock_flow.slice(-5).reduce((sum, r) => sum + r.net_wan, 0) >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {fmtWan(flowData.stock_flow.slice(-5).reduce((sum, r) => sum + r.net_wan, 0))}
                </b>
              </>
            )}
          </div>
        )}
      </div>

      {/* 财务摘要 */}
      <div className="card section-gap">
        <div className="h-sec">
          <h2 style={{ margin: 0 }}>财务摘要 <span className="tag">最新财报 {s.fina && s.fina[0] ? s.fina[0].end_date : 'n/a'}</span></h2>
        </div>
        <div className="fina-grid">
          <div><span>PE (TTM)</span><b className="num">{num(f.pe)}</b></div>
          <div><span>PB</span><b className="num">{num(f.pb)}</b></div>
          <div><span>总市值</span><b className="num">{f.total_mv_wan ? (f.total_mv_wan / 1e4).toFixed(0) + ' 亿' : 'n/a'}</b></div>
          <div><span>流通市值</span><b className="num">{f.circ_mv_wan ? (f.circ_mv_wan / 1e4).toFixed(0) + ' 亿' : 'n/a'}</b></div>
          <div><span>换手率</span><b className="num">{f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : 'n/a'}</b></div>
          <div><span>量比</span><b className="num">{f.volume_ratio != null ? f.volume_ratio.toFixed(2) : 'n/a'}</b></div>
          {s.fina && s.fina[0] && (
            <>
              <div><span>ROE (加权)</span><b className="num">{s.fina[0].roe_waa != null ? s.fina[0].roe_waa.toFixed(2) + '%' : 'n/a'}</b></div>
              <div><span>ROA</span><b className="num">{s.fina[0].roa != null ? s.fina[0].roa.toFixed(2) + '%' : 'n/a'}</b></div>
              <div><span>毛利率</span><b className="num">{s.fina[0].grossprofit_margin != null ? s.fina[0].grossprofit_margin.toFixed(2) + '%' : 'n/a'}</b></div>
              <div><span>净利率</span><b className="num">{s.fina[0].netprofit_margin != null ? s.fina[0].netprofit_margin.toFixed(2) + '%' : 'n/a'}</b></div>
              <div><span>营收增速</span>
                <b className="num" style={{ color: (s.fina[0].or_yoy ?? 0) >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {s.fina[0].or_yoy != null ? s.fina[0].or_yoy.toFixed(2) + '%' : 'n/a'}
                </b></div>
              <div><span>净利增速</span>
                <b className="num" style={{ color: (s.fina[0].netprofit_yoy ?? 0) >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {s.fina[0].netprofit_yoy != null ? s.fina[0].netprofit_yoy.toFixed(2) + '%' : 'n/a'}
                </b></div>
              <div><span>资产负债率</span><b className="num">{s.fina[0].debt_to_assets != null ? s.fina[0].debt_to_assets.toFixed(2) + '%' : 'n/a'}</b></div>
              <div><span>经营现金流/营收</span><b className="num">{s.fina[0].ocf_to_or != null ? s.fina[0].ocf_to_or.toFixed(2) : 'n/a'}</b></div>
              <div><span>EPS</span><b className="num">{num(s.fina[0].eps)}</b></div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
