import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, OverviewItem, OverviewResp, SectorFlowResp, ScanStatus, HealthResp, SetupStatus, MoneyHeatmapResp } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import SectorFlowPanel from '../components/SectorFlowPanel'
import MoneyHeatmap from '../components/MoneyHeatmap'
import {
  loadOverviewCache,
  loadParams,
  loadPoolPref,
  saveOverviewCache,
  saveParams,
} from '../scanCache'
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
  const cached = loadOverviewCache()
  const prefPool = loadPoolPref()
  const prefParams = loadParams()
  const [data, setData] = useState<OverviewResp | null>(cached?.data ?? null)
  const [health, setHealth] = useState<HealthResp | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [sector, setSector] = useState<SectorFlowResp | null>(null)
  const [sectorDays, setSectorDays] = useState(10)
  const [heatmap, setHeatmap] = useState<MoneyHeatmapResp | null>(null)
  const [heatErr, setHeatErr] = useState('')
  const [pool, setPool] = useState<'A' | 'B' | 'ALL'>(prefPool || cached?.pool || 'A')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [cacheNote, setCacheNote] = useState(
    cached?.data?.items?.length
      ? `已恢复上次扫描（${cached.savedAt?.slice(0, 19).replace('T', ' ') || ''}），再次扫描前会一直保留`
      : '',
  )
  const [scanning, setScanning] = useState(false)
  const [scanTask, setScanTask] = useState<string | null>(null)
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null)
  const [topN, setTopN] = useState(prefParams?.topN ?? 20)
  const [days, setDays] = useState(prefParams?.days ?? 160)
  const c = useChartColors()
  /** 扫描 API 尚未返回 task_id 时用户已点取消 */
  const pendingCancelRef = useRef(false)

  // A5 竞态守卫：序号 + AbortController，保证只有最新一次请求用于渲染
  const overviewSeq = useRef(0)
  const overviewReqRef = useRef<AbortController | null>(null)
  const sectorReqRef = useRef<AbortController | null>(null)

  // B13 解耦：板块资金流与个股列表（overview）相互独立，切换 sectorDays 只重拉板块层
  const loadOverview = useCallback((opts?: { keepOnFail?: boolean }) => {
    const keepOnFail = opts?.keepOnFail !== false
    const seq = ++overviewSeq.current
    overviewReqRef.current?.abort()
    const ac = new AbortController()
    overviewReqRef.current = ac
    const signal = ac.signal

    setLoading(true)
    api.health({ signal }).then(setHealth).catch(() => setHealth(null))
    api.setupStatus({ signal }).then(setSetup).catch(() => setSetup(null))
    api.overview(pool, { signal, timeoutMs: 60_000 })
      .then((resp) => {
        setLoading(false)
        if (overviewSeq.current !== seq) return
        // 服务端空列表时：若本地有缓存且非刚扫完，优先保留缓存，避免「进详情返回变空」
        if ((!resp.items || resp.items.length === 0) && keepOnFail) {
          const c2 = loadOverviewCache()
          if (c2?.data?.items?.length) {
            setData(c2.data)
            setCacheNote('服务端暂无新列表，已继续显示上次扫描结果')
            setErr('')
            return
          }
        }
        setData(resp)
        if (resp.items?.length) {
          saveOverviewCache(pool, resp)
          setCacheNote('')
        }
        if (resp.empty_reason && !resp.items?.length) {
          setErr(resp.empty_reason)
        } else {
          setErr('')
        }
      })
      .catch((e: unknown) => {
        setLoading(false)
        if (overviewSeq.current !== seq) return // 切池等新请求已接管，忽略旧失败
        const isAbort = (e as { name?: string })?.name === 'AbortError'
        if (keepOnFail) {
          const c2 = loadOverviewCache()
          if (c2?.data?.items?.length) {
            setData(c2.data)
            setCacheNote(isAbort ? '请求超时（后端计算较慢），已显示上次扫描缓存' : '网络/接口异常，已显示上次扫描缓存')
            setErr('')
            return
          }
        }
        if (isAbort) return // 被新请求中止且无可显示缓存
        setErr(String(e))
      })
  }, [pool])

  const loadSector = useCallback(() => {
    sectorReqRef.current?.abort()
    const ac = new AbortController()
    sectorReqRef.current = ac
    api.sectorFlow(sectorDays, { signal: ac.signal })
      .then(setSector)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === 'AbortError') return
        setSector(null)
      })
  }, [sectorDays])

  // 最新交易日资金热力图（挂载 + 扫描完成后刷新）
  const loadHeatmap = useCallback(() => {
    api.moneyHeatmap(24)
      .then((h) => { setHeatmap(h); setHeatErr('') })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === 'AbortError') return
        setHeatErr(String(e))
      })
  }, [])

  useEffect(() => { loadHeatmap() }, [loadHeatmap])

  useEffect(() => {
    loadOverview({ keepOnFail: true })
  }, [loadOverview])

  // 挂载时恢复扫描状态：切走页面组件卸载后状态丢失，但后端扫描线程仍在跑——
  // 回来时发现 running/pending 任务就重新对接轮询（后台扫描不中断）。
  useEffect(() => {
    let mounted = true
    api.scanStatus()
      .then((st) => {
        if (!mounted) return
        if (
          st &&
          st.id &&
          (st.status === 'running' || st.status === 'pending' || st.status === 'cancelling')
        ) {
          setScanning(true)
          setScanTask(st.id)
          setScanStatus(st)
          setCacheNote(
            st.status === 'cancelling' || st.cancel_requested
              ? '检测到扫描正在取消，已恢复进度显示'
              : '检测到后台扫描进行中，已恢复进度显示',
          )
        } else if (st && st.status === 'done') {
          setCacheNote('上次后台扫描已完成，列表已可查看')
        }
      })
      .catch(() => undefined)
    return () => { mounted = false }
  }, [])

  useEffect(() => {
    loadSector()
  }, [loadSector])

  useEffect(() => {
    return () => {
      overviewReqRef.current?.abort()
      sectorReqRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    saveParams(topN, days)
  }, [topN, days])

  // 扫描进度轮询：取消中加速；客户端超时兜底；失败指数退避
  useEffect(() => {
    if (!scanning || !scanTask) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let interval = 1500
    let inflight = false
    let cancelSince = 0
    let lastResend = 0

    const finish = (st: ScanStatus) => {
      setScanning(false)
      setScanTask(null)
      pendingCancelRef.current = false
      if (st.status === 'done') {
        api.overview('ALL')
          .then((all) => {
            if (all.items?.length) saveOverviewCache('ALL', all)
          })
          .finally(() => {
            loadOverview({ keepOnFail: false })
            setCacheNote('扫描完成，列表已更新（进详情返回仍会保留）')
          })
      } else if (st.status === 'error') {
        setErr(`扫描失败: ${st.error || ''}`)
        setCacheNote('扫描失败，已保留上次列表')
      } else if (st.status === 'cancelled') {
        setErr('')
        setCacheNote(
          st.stage?.includes('强制')
            ? '扫描已强制结束（卡住任务已解锁），已保留上次列表'
            : '扫描已取消，已保留上次列表',
        )
      }
    }

    const tick = async () => {
      if (inflight) return
      inflight = true
      try {
        const st = await api.scanStatus(scanTask)
        if (stopped) return
        setScanStatus(st)
        const cancelling = st.status === 'cancelling' || !!st.cancel_requested
        interval = cancelling ? 600 : 1500
        if (cancelling) {
          if (!cancelSince) cancelSince = Date.now()
          // 5s 仍未终态：再发一次 cancel（幂等）
          if (Date.now() - lastResend > 5000) {
            lastResend = Date.now()
            api.cancelScan(scanTask).catch(() => undefined)
          }
          // 15s 客户端兜底：解锁 UI（后端看门狗约 10s）
          if (Date.now() - cancelSince > 15000) {
            finish({
              id: scanTask,
              status: 'cancelled',
              stage: '已取消（前端超时收口）',
              progress: st.progress ?? 0,
              cancel_requested: true,
            })
            return
          }
        } else {
          cancelSince = 0
        }
        if (st.status === 'done' || st.status === 'error' || st.status === 'cancelled') {
          finish(st)
          return
        }
      } catch {
        interval = Math.min(interval * 2, 15_000)
      } finally {
        inflight = false
      }
      if (!stopped) timer = setTimeout(tick, interval)
    }

    timer = setTimeout(tick, 400)
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [scanning, scanTask, loadOverview])

  const onScan = async () => {
    pendingCancelRef.current = false
    setScanning(true)
    setErr('')
    setCacheNote('扫描中…完成后将替换列表；取消/失败则保留上次结果')
    setScanStatus({ id: '', status: 'pending', stage: '排队中', progress: 0, cancel_requested: false })
    try {
      const resp = await api.scan(topN, days, false)
      setScanTask(resp.task_id)
      // 用户在拿到 task_id 前已点取消 → 立即发取消
      if (pendingCancelRef.current) {
        pendingCancelRef.current = false
        setScanStatus({
          id: resp.task_id,
          status: 'cancelling',
          stage: '取消中…正在停止工作进程',
          progress: 0,
          cancel_requested: true,
        })
        try {
          await api.cancelScan(resp.task_id)
        } catch (e) {
          setErr(`取消失败: ${String(e)}`)
        }
      }
    } catch (e) {
      setErr(String(e))
      setScanning(false)
      pendingCancelRef.current = false
    }
  }

  const onCancel = async () => {
    pendingCancelRef.current = true
    // 立即给反馈，避免「点了没反应」
    setScanStatus((prev) => ({
      id: scanTask || prev?.id || '',
      status: 'cancelling',
      stage: '取消中…正在停止工作进程',
      progress: prev?.progress ?? 0,
      cancel_requested: true,
      result: prev?.result ?? null,
      error: prev?.error ?? null,
    }))
    setCacheNote('已请求取消，等待当前分片/数据加载结束…')
    setErr('')

    let tid = scanTask
    if (!tid) {
      try {
        const st = await api.scanStatus()
        if (st?.id && (st.status === 'running' || st.status === 'pending' || st.status === 'cancelling')) {
          tid = st.id
          setScanTask(st.id)
        }
      } catch {
        /* ignore */
      }
    }
    if (!tid) {
      // task_id 尚未返回：等 onScan 拿到 id 后自动 cancel（pendingCancelRef）
      setCacheNote('已请求取消，等待任务号返回后停止…')
      return
    }
    try {
      const r = await api.cancelScan(tid)
      setScanStatus((prev) => ({
        id: tid!,
        status: (r.status as ScanStatus['status']) || 'cancelling',
        stage: r.stage || '取消中…正在停止工作进程',
        progress: prev?.progress ?? 0,
        cancel_requested: true,
        result: prev?.result ?? null,
        error: prev?.error ?? null,
      }))
    } catch (e) {
      setErr(`取消失败: ${String(e)}`)
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
  if (!data) return <div className="loading">加载中…（若刚扫过，请稍候或点扫描后的列表缓存）</div>

  const avgScore = data.items.length ? data.items.reduce((s, x) => s + x.score, 0) / data.items.length : 0
  const totalFlow = data.items.reduce((s, x) => s + (x.fund_net_wan || 0), 0)
  const fresh = data.freshness || health?.freshness
  const regime = data.regime || health?.regime
  const stale = !!fresh?.is_stale
  const defense = regime?.allow_new_entries === false

  return (
    <div>
      {/* 小白提示条 */}
      <div className="card section-gap" style={{ marginBottom: 12, borderColor: 'var(--accent)', background: 'var(--surface-2)' }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>新手 3 步</div>
        <div className="muted" style={{ lineHeight: 1.7 }}>
          ① 双击 <b>一键启动.bat</b> 同步数据（日常约 2～10 分钟）→
          ② 点下方 <b>扫描</b>（约 5～15 分钟；横盘优先 6 个月，不够再降到 5/4/… 凑满约 20 只）→
          ③ 只看 <b>A 池</b>（可交易）；B 池仅观察。
          <br />
          进详情再返回：<b>会保留上次扫描结果</b>，直到你再次扫描成功。
          {cacheNote && (
            <span style={{ color: 'var(--accent)', marginLeft: 6 }}>（{cacheNote}）</span>
          )}
          {defense && (
            <span style={{ color: 'var(--up)' }}> 当前防守环境，A 池可能为空，属正常风控。</span>
          )}
          {setup && !setup.has_market_data && (
            <span style={{ color: 'var(--up)' }}> 尚未拉到行情，请先跑一键启动同步。</span>
          )}
          {setup && !setup.has_token && (
            <span style={{ color: 'var(--up)' }}> 未检测到 Token，请编辑项目根目录 .env。</span>
          )}
        </div>
      </div>

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
          {loading && <span className="muted">⏳ 加载中…</span>}
          <span><b>A池 Top</b></span>
          <input type="number" value={topN} min={5} max={30} step={1}
            onChange={(e) => setTopN(Number(e.target.value))}
            style={{ width: 64, background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, padding: '5px 8px' }} />
          <span>回看</span>
          <input type="number" value={days} min={60} max={250} step={10}
            onChange={(e) => setDays(Number(e.target.value))}
            style={{ width: 72, background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, padding: '5px 8px' }} />
          <button className="btn primary" onClick={onScan} disabled={scanning}>
            {scanning
              ? (scanStatus?.status === 'cancelling' || scanStatus?.cancel_requested
                ? '取消中…'
                : '扫描中…')
              : '🚀 扫描(A池优先)'}
          </button>
          {scanning && (
            <button
              className="btn"
              onClick={onCancel}
              disabled={scanStatus?.status === 'cancelling' || !!scanStatus?.cancel_requested}
              style={{ borderColor: 'var(--up)', color: 'var(--up)' }}
              title={scanTask ? `取消任务 ${scanTask}` : '取消当前扫描'}
            >
              {scanStatus?.status === 'cancelling' || scanStatus?.cancel_requested
                ? '⏹ 取消中…'
                : '⏹ 取消'}
            </button>
          )}
        </div>
        {scanning && scanStatus && (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
              <span>
                {scanStatus.stage}
                {(scanStatus.status === 'cancelling' || scanStatus.cancel_requested) && (
                  <span className="badge badge-warn" style={{ marginLeft: 8 }}>取消请求已发送</span>
                )}
              </span>
              <span>{scanStatus.progress}%</span>
            </div>
            <div style={{ height: 8, background: 'var(--surface-2)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${Math.max(scanStatus.progress, scanStatus.status === 'cancelling' ? 8 : 0)}%`,
                background:
                  scanStatus.status === 'cancelling' || scanStatus.cancel_requested
                    ? 'linear-gradient(90deg, var(--warn), var(--up))'
                    : 'linear-gradient(90deg, var(--accent), var(--accent-2))',
                borderRadius: 4, transition: 'width 0.4s ease',
              }} />
            </div>
            {scanTask && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--muted)' }} className="mono">
                task {scanTask}
              </div>
            )}
          </div>
        )}
        {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      {/* 最新交易日资金热力图（treemap，nivo 风格） */}
      <div className="card" style={{ marginBottom: 16 }}>
        {heatErr ? (
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>资金热力图不可用：{heatErr}</div>
        ) : heatmap ? (
          <MoneyHeatmap data={heatmap} />
        ) : (
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>加载资金热力图…</div>
        )}
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
