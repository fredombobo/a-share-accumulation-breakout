import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, OverviewItem, OverviewResp, SectorFlowResp, ScanStatus, HealthResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import SectorFlowPanel from '../components/SectorFlowPanel'
import type { EChartsOption } from 'echarts'

function tierBadge(tier?: string, pool?: string, tradeable?: boolean) {
  if (pool === 'A' && (tradeable || tier === 'strict')) return { text: '可交易', cls: 'badge badge-ok' }
  if (pool === 'A') return { text: 'A池', cls: 'badge badge-ok' }
  const t = (tier || '').toLowerCase()
  if (t === 'relaxed') return { text: '放宽观察', cls: 'badge badge-warn' }
  if (t.includes('theme') || t === 'theme_fill') return { text: '主题观察', cls: 'badge badge-mute' }
  if (t === 'unknown') return { text: '旧数据', cls: 'badge badge-warn' }
  return { text: pool === 'B' ? '观察' : (tier || '—'), cls: 'badge' }
}

export default function Overview() {
  const nav = useNavigate()
  const [data, setData] = useState<OverviewResp | null>(null)
  const [health, setHealth] = useState<HealthResp | null>(null)
  const [sector, setSector] = useState<SectorFlowResp | null>(null)
  const [sectorDays, setSectorDays] = useState(10)
  const [pool, setPool] = useState<'A' | 'B' | 'ALL'>('A')
  const [err, setErr] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanTask, setScanTask] = useState<string | null>(null)
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null)
  const [topN, setTopN] = useState(15)
  const [days, setDays] = useState(160)
  const c = useChartColors()

  const load = () => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.overview(pool).then(setData).catch((e) => setErr(String(e)))
    api.sectorFlow(sectorDays).then(setSector).catch(() => setSector(null))
  }
  useEffect(load, [sectorDays, pool])

  useEffect(() => {
    if (!scanning || !scanTask) return
    const timer = setInterval(async () => {
      try {
        const st = await api.scanStatus(scanTask)
        setScanStatus(st)
        if (st.status === 'done' || st.status === 'error' || st.status === 'cancelled') {
          setScanning(false)
          setScanTask(null)
          clearInterval(timer)
          if (st.status === 'done') load()
          else if (st.status === 'error') setErr(`扫描失败: ${st.error || ''}`)
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(timer)
  }, [scanning, scanTask])

  const onScan = async () => {
    setScanning(true)
    setErr('')
    setScanStatus({ id: '', status: 'pending', stage: '排队中', progress: 0, cancel_requested: false })
    try {
      const resp = await api.scan(topN, days, false)
      setScanTask(resp.task_id)
    } catch (e) {
      setErr(String(e))
      setScanning(false)
    }
  }

  const onCancel = async () => {
    if (scanTask) {
      try { await api.cancelScan(scanTask) } catch { /* ignore */ }
    }
  }

  const miniOption = (it: OverviewItem): EChartsOption | null => {
    const k = it.kline || []
    if (!k.length) return null
    const cat = k.map((d) => d.trade_date)
    const candle = k.map((d) => [d.open, d.close, d.low, d.high])
    const boxHigh = it.box_high
    const boxLow = it.box_low
    const lastDate = cat[cat.length - 1]
    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 2, right: 2, top: 6, bottom: 2 },
      xAxis: { type: 'category' as const, data: cat, show: false },
      yAxis: { type: 'value' as const, scale: true, show: false },
      tooltip: { trigger: 'axis' as const, confine: true },
      series: [
        {
          type: 'candlestick' as const,
          data: candle,
          itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
          barWidth: '70%',
        },
        ...(boxHigh != null && boxLow != null
          ? [
              { type: 'line' as const, data: cat.map(() => boxHigh), symbol: 'none', lineStyle: { color: c.warn, width: 1, type: 'dashed' as const }, z: 5 },
              { type: 'line' as const, data: cat.map(() => boxLow), symbol: 'none', lineStyle: { color: c.accent2, width: 1, type: 'dashed' as const }, z: 5 },
              {
                type: 'scatter' as const,
                data: [[lastDate, it.price ?? boxHigh]],
                symbol: 'pin',
                symbolSize: 26,
                itemStyle: { color: c.warn },
                label: { show: true, formatter: '突', color: '#fff', fontSize: 10 },
                z: 10,
              },
            ]
          : []),
      ],
    }
  }

  if (err && !data) return <div className="err">加载失败：{err}</div>
  if (!data) return <div className="loading">加载中…</div>

  const avgScore = data.items.length ? data.items.reduce((s, x) => s + x.score, 0) / data.items.length : 0
  const totalFlow = data.items.reduce((s, x) => s + (x.fund_net_wan || 0), 0)
  const fresh = data.freshness || health?.freshness
  const regime = data.regime || health?.regime
  const stale = !!fresh?.is_stale

  return (
    <div>
      {/* 状态条：数据新鲜度 + 市场环境 */}
      <div className={`status-bar ${stale ? 'status-stale' : 'status-ok'}`}>
        <span>
          数据 <b>{data.as_of || fresh?.as_of || '—'}</b>
          {fresh && (
            <span className={`badge ${stale ? 'badge-danger' : fresh.stale_days > 0 ? 'badge-warn' : 'badge-ok'}`} style={{ marginLeft: 8 }}>
              {fresh.label}
              {fresh.stale_label
                ? ` · ${fresh.stale_label}`
                : ` · 滞后 ${fresh.stale_days} 个${fresh.unit === 'trading' ? '交易日' : '日历日'}`}
            </span>
          )}
        </span>
        <span>
          环境{' '}
          <b className={regime?.regime === 'defense' ? 'text-danger' : regime?.regime === 'attack' ? 'text-ok' : ''}>
            {regime?.label || '—'}
          </b>
          {regime?.allow_new_entries === false && (
            <span className="badge badge-danger" style={{ marginLeft: 8 }}>禁止新开仓</span>
          )}
        </span>
        <span className="muted">A 池=可交易 strict · B 池=观察（不混排）</span>
      </div>

      <div className="metrics">
        <div className="metric"><div className="label">当前池</div><div className="value">{pool}<span className="sub"> / {data.count}只</span></div></div>
        <div className="metric"><div className="label">平均综合分</div><div className="value">{avgScore.toFixed(1)}</div></div>
        <div className="metric"><div className="label">合计主力净流入</div><div className="value">{(totalFlow / 10000).toFixed(2)}<span className="sub"> 亿</span></div></div>
        <div className="metric"><div className="label">可交易票</div><div className="value">{data.items.filter((x) => x.tradeable).length}</div></div>
      </div>

      <div className="card section-gap" style={{ marginBottom: 16 }}>
        <div className="row" style={{ flexWrap: 'wrap', gap: 10 }}>
          <span><b>池：</b></span>
          {(['A', 'B', 'ALL'] as const).map((p) => (
            <button
              key={p}
              className="btn"
              style={{ borderColor: pool === p ? 'var(--accent)' : 'var(--border)', color: pool === p ? 'var(--accent)' : 'var(--text)' }}
              onClick={() => setPool(p)}
            >
              {p === 'A' ? 'A 可交易' : p === 'B' ? 'B 观察' : '全部'}
            </button>
          ))}
          <span><b>A池 Top</b></span>
          <input type="number" value={topN} min={5} max={30} step={1}
            onChange={(e) => setTopN(Number(e.target.value))}
            style={{ width: 64, background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, padding: '5px 8px' }} />
          <span>回看</span>
          <input type="number" value={days} min={60} max={250} step={10}
            onChange={(e) => setDays(Number(e.target.value))}
            style={{ width: 72, background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, padding: '5px 8px' }} />
          <button className="btn primary" onClick={onScan} disabled={scanning}>
            {scanning ? '扫描中…' : '🚀 扫描(A池优先)'}
          </button>
          {scanning && scanTask && (
            <button className="btn" onClick={onCancel} style={{ borderColor: 'var(--up)', color: 'var(--up)' }}>⏹ 取消</button>
          )}
        </div>
        {scanning && scanStatus && (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
              <span>{scanStatus.stage}</span>
              <span>{scanStatus.progress}%</span>
            </div>
            <div style={{ height: 8, background: 'var(--surface-2)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                height: '100%', width: `${scanStatus.progress}%`,
                background: 'linear-gradient(90deg, var(--accent), var(--accent-2))',
                borderRadius: 4, transition: 'width 0.4s ease',
              }} />
            </div>
          </div>
        )}
        {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>🏭 板块资金流 <span className="tag">观察建仓/出逃</span></h2>
          <div className="row">
            {[5, 10, 20].map((n) => (
              <button key={n} className="btn" style={{ padding: '4px 10px', borderColor: sectorDays === n ? 'var(--accent)' : 'var(--border)', color: sectorDays === n ? 'var(--accent)' : 'var(--text)' }}
                onClick={() => setSectorDays(n)}>{n}日</button>
            ))}
          </div>
        </div>
        {sector && <SectorFlowPanel data={sector} />}
      </div>

      <div className="stock-grid">
        {data.items.map((it) => {
          const b = tierBadge(it.tier, it.pool, it.tradeable)
          return (
            <div key={it.ts_code} className="stock-card" onClick={() => nav(`/stock/${it.ts_code}`)}>
              <div className="head">
                <div>
                  <span className="name">{it.name}</span>
                  <span className="code">{it.code}</span>
                  <span className={b.cls} style={{ marginLeft: 6 }}>{b.text}</span>
                </div>
                <span className="score">{it.score.toFixed(1)}</span>
              </div>
              <div className="meta">
                <span>价 <b>{it.price?.toFixed(2) ?? 'n/a'}</b></span>
                <span>行业 <b>{it.industry}</b></span>
                <span>市值 <b>{it.mv_yi?.toFixed(0) ?? 'n/a'}亿</b></span>
                <span>量比 <b>{it.vol_ratio?.toFixed(1) ?? 'n/a'}x</b></span>
              </div>
              {it.trade && (
                <div className="meta trade-row">
                  <span>止损 <b className="text-danger">{it.trade.stop_loss ?? '—'}</b></span>
                  <span>目标1 <b className="text-ok">{it.trade.target_1 ?? '—'}</b></span>
                  <span>仓位 <b>{it.trade.position_pct}%</b></span>
                  <span>持有≤ <b>{it.trade.max_hold_days}日</b></span>
                </div>
              )}
              {miniOption(it) && <EChart option={miniOption(it)!} height={110} />}
              <div className="reason">{it.reasons.split('；').filter(Boolean).slice(0, 4).map((r, i) => (
                <span key={i}>{i === 0 ? <b>✓ </b> : <span>· </span>}{r}{' '}</span>
              ))}</div>
            </div>
          )
        })}
      </div>
      {!data.items.length && (
        <div className="card muted" style={{ padding: 24, textAlign: 'center' }}>
          当前池无标的。{regime?.allow_new_entries === false ? '防守环境已禁止新开仓。' : '请运行扫描或切换到 B 观察池。'}
        </div>
      )}
    </div>
  )
}
