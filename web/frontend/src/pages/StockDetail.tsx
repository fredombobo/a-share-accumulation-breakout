import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router'
import { api, StockDetail as Detail, StockFlowResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import FundFlowChart from '../components/FundFlowChart'
import AIReviewPanel from '../components/AIReviewPanel'
import { IcoArrowRight } from '../components/Icons'
import type { EChartsOption } from 'echarts'

const num = (x: number | null | undefined, d = 2) => (x == null || isNaN(x as number) ? '—' : (x as number).toFixed(d))
// 输入单位：万元
const fmtWan = (v: number) => {
  const abs = Math.abs(v)
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + ' 亿'
  if (abs >= 1e2) return v.toFixed(1) + ' 万'
  return v.toFixed(0) + ' 万'
}

export default function StockDetail() {
  const { tsCode = '' } = useParams()
  const [searchParams] = useSearchParams()
  const runId = searchParams.get('run_id') || undefined
  const [s, setS] = useState<Detail | null>(null)
  const [flowData, setFlowData] = useState<StockFlowResp | null>(null)
  const [flowDays, setFlowDays] = useState(20)
  const [err, setErr] = useState('')
  const [flowError, setFlowError] = useState(false)
  const nav = useNavigate()
  const c = useChartColors()

  useEffect(() => {
    let active = true
    setS(null)
    setErr('')
    const code = decodeURIComponent(tsCode)
    ;(runId ? api.stock(code, undefined, runId) : api.stock(code))
      .then(data => { if (active) setS(data) })
      .catch((e) => { if (active) setErr(String(e)) })
    return () => { active = false }
  }, [tsCode, runId])

  useEffect(() => {
    let active = true
    setFlowData(null)
    setFlowError(false)
    if (runId) return () => { active = false }
    api.stockFlow(decodeURIComponent(tsCode), flowDays)
      .then(data => { if (active) setFlowData(data) })
      .catch(() => { if (active) setFlowError(true) })
    return () => { active = false }
  }, [tsCode, flowDays, runId])

  const klineOpt = useMemo<EChartsOption | null>(() => {
    if (!s) return null
    const ohlc = s.kline || []
    const cat = ohlc.map((d) => d.trade_date)
    const candle = ohlc.map((d) => [d.open, d.close, d.low, d.high])
    const vol = ohlc.map((d) => d.vol)
    const zoomStart = ohlc.length > 120 ? 100 * (1 - 120 / ohlc.length) : 0
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
      color: [c.up, c.accent, c.warn, c.accent2],
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
        { type: 'inside' as const, xAxisIndex: [0, 1], start: zoomStart, end: 100 },
        { type: 'slider' as const, xAxisIndex: [0, 1], start: zoomStart, end: 100, bottom: 2, height: 16, borderColor: c.axis, textStyle: { color: c.subtext } },
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
        { name: 'MA5', type: 'line' as const, data: ma5, smooth: true, symbol: 'none', itemStyle: { color: c.accent }, lineStyle: { width: 1.2, color: c.accent }, connectNulls: true },
        { name: 'MA10', type: 'line' as const, data: ma10, smooth: true, symbol: 'none', itemStyle: { color: c.warn }, lineStyle: { width: 1.2, color: c.warn }, connectNulls: true },
        { name: 'MA20', type: 'line' as const, data: ma20, smooth: true, symbol: 'none', itemStyle: { color: c.accent2 }, lineStyle: { width: 1.2, color: c.accent2 }, connectNulls: true },
        {
          name: '成交量', type: 'bar' as const, xAxisIndex: 1, yAxisIndex: 1, data: vol,
          itemStyle: { color: (p: any) => { const d = ohlc[p.dataIndex]; return d ? (d.close >= d.open ? c.up : c.down) : c.up } },
        },
      ],
    }
  }, [s, c])

  if (err) return <div className="card" role="alert"><h2>暂时无法载入个股</h2><p className="muted">{err}</p><button className="btn" onClick={() => nav('/')}>返回每日选股</button></div>
  if (!s) return <div className="page-loading" role="status"><span className="loading-line" /><p>正在整理个股证据</p><span className="muted">读取价格、信号和财务数据…</span></div>

  const f = s.fundamentals
  const sig = s.signal
  const priceUp = (f.close ?? 0) >= (s.kline?.length >= 2 ? s.kline[s.kline.length - 2].close : f.close ?? 0)
  const candidateLabel = s.candidate_status === 'CURRENT_CANDIDATE' ? (s.pool === 'A' ? '本次严格候选' : '本次观察名单')
    : s.candidate_status === 'HISTORICAL_CANDIDATE' ? '历史候选' : '自由查询 · 未在本次名单'
  const recentFlow = flowData?.stock_flow.slice(-5) || []
  const recentFlowTotal = flowData?.calendar_verified && recentFlow.length === 5 && recentFlow.every(row => row.net_wan != null)
    ? recentFlow.reduce((sum, row) => sum + row.net_wan!, 0) : null

  return (
    <div className="stock-workspace fade-up">
      {/* 头部英雄条 */}
      <div className="stock-hero">
        <div className="stock-hero-main">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <h1 className="stock-hero-name" style={{ margin: 0 }}>{s.name}</h1>
            <span className="mono" style={{ color: 'var(--muted)', fontSize: 14 }}>{s.ts_code}</span>
            <span className="pill accent">{s.industry}</span>
            <span className={`pill ${s.candidate_status === 'CURRENT_CANDIDATE' ? '' : 'warn'}`}>{candidateLabel}</span>
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
          <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>行情 {s.quote_as_of || s.as_of} · 每日指标 {s.fundamentals_as_of || '未记录'}{s.scan_as_of ? ` · 扫描 ${s.scan_as_of}` : ''}</div>
        </div>
        <div className="stock-hero-actions">
          {!runId && <button className="btn primary" onClick={() => document.getElementById('ai-review')?.scrollIntoView({ behavior: 'smooth' })}>
            AI 证据评测
          </button>}
          <button className="btn" onClick={() => nav(runId ? `/?run_id=${encodeURIComponent(runId)}` : '/')}>
            返回总览 <IcoArrowRight size={13} style={{ transform: 'rotate(180deg)' }} />
          </button>
        </div>
      </div>

      {runId && <div className="detail-context-note">历史记录 {s.run_id || runId} · 入选证据沿用当次记录。K 线和财务公告按扫描日截取，但可能包含后续数据修订；行业与名称来自当前目录。</div>}
      {runId && !s.publication?.verified && <div className="detail-context-note">此历史记录未通过当前发布协议核验，仅供核对旧记录，不代表当前有效候选。</div>}
      {s.candidate_status === 'QUERY_ONLY' && <div className="detail-context-note">这只股票是自由查询结果。未取得本次扫描的入选证据，因此不显示默认突破信号或交易建议。</div>}
      <div className="two-col section-gap detail-evidence-layout">
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
              <h2 style={{ margin: 0 }}>扫描信号证据</h2>
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
              <h2 style={{ margin: 0 }}>5 日资金窗口</h2>
              <span className="muted" style={{ fontSize: 10 }}>数据源统计口径</span>
            </div>
            <div className="sig-grid">
              <div><span>资金净流入</span>
                <b className="num" style={{ color: s.fund_flow.net_wan == null ? 'var(--muted)' : s.fund_flow.net_wan >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {s.fund_flow.net_wan == null ? '未取得' : fmtWan(s.fund_flow.net_wan)}
                </b></div>
              <div><span>{s.fund_flow.window?.denominator_basis === 'large_plus_extra_large_buy_and_sell' ? '净额 / 大单双边额' : '资金强度比'}</span><b className="num">{num(s.fund_flow.ratio_pct)}{s.fund_flow.ratio_pct == null ? '' : '%'}</b></div>
              <div><span>资金强度分</span><b className="num">{num(s.fund_flow.score, 1)} / 100</b></div>
            </div>
          </div>

          <div className="note">{s.fund_flow.complete ? '窗口完整' : '窗口未通过完整性核对'} · {(s.fund_flow.window?.observed_dates || []).join(' / ') || '日期未记录'}<br />{s.fund_flow.window?.reason || '旧记录缺少逐日窗口证据'}<br />{s.fund_flow.window?.basis_note || '请结合数据源定义解释资金净额。'}</div>
          <div className="detail-research-link">
            <strong>从信号到验证</strong>
            <p>价格结构用于核对入场依据。止盈、止损与持有期统一在研究回测中设定，并结合历史结果评估。</p>
            <button className="btn" onClick={() => nav('/backtest')}>查看研究回测 <IcoArrowRight size={12} /></button>
          </div>
        </div>
      </div>

      {/* 资金流趋势 */}
      {!runId && <div className="card section-gap">
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
            stockNet={flowData.stock_flow.map((r) => r.net_wan == null ? null : r.net_wan * 10000)}
            stockBuy={flowData.stock_flow.map((r) => r.buy_main_wan == null ? null : r.buy_main_wan * 10000)}
            stockSell={flowData.stock_flow.map((r) => r.sell_main_wan == null ? null : r.sell_main_wan * 10000)}
            sectorDates={(flowData.sector_flow.dates || []).map((d) => d.slice(4))}
            sectorNet={(flowData.sector_flow.net_wan || []).map((v) => v == null ? null : v * 10000)}
            height={320}
          />
        ) : (
          <div className="loading" role="status">{flowError ? '资金流暂不可用，可切换周期重试' : flowData ? '所选周期暂无资金流数据' : '资金流数据加载中…'}</div>
        )}
        {flowData && (
          <div className="note">
            红柱=资金净流入，绿柱=净流出；虚线=大单及特大单买入金额。板块按已取得的资金记录汇总。缺失日期留空，连续净流入不代表已证实的主力建仓。
            {flowData.calendar_verified === false && <span> 交易日历未核对，当前仅展示已有数据日期。</span>}
            {!!flowData.missing_dates?.length && <span> 缺少完整记录：{flowData.missing_dates.join('、')}。</span>}
            {flowData.stock_flow.length >= 5 && (
              <>
                近5日个股累计：
                <b style={{ color: recentFlowTotal == null ? 'var(--muted)' : recentFlowTotal >= 0 ? 'var(--up-ink)' : 'var(--down-ink)' }}>
                  {recentFlowTotal == null ? '数据未齐' : fmtWan(recentFlowTotal)}
                </b>
              </>
            )}
          </div>
        )}
      </div>

      }
      {/* 财务摘要 */}
      <div className="card section-gap">
        <div className="h-sec">
          <h2 style={{ margin: 0 }}>财务摘要 <span className="tag">最新财报 {s.fina && s.fina[0] ? s.fina[0].end_date : 'n/a'}</span></h2>
        </div>
        <div className="fina-grid">
          <div><span>PE</span><b className="num">{num(f.pe)}</b></div>
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
      {!runId && <div className="section-gap"><AIReviewPanel tsCode={s.ts_code} /></div>}
    </div>
  )
}
