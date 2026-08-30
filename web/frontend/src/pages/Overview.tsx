import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  api,
  type ClassificationCatalogResp,
  type ClassificationKey,
  HealthResp,
  MoneyHeatmapResp,
  OverviewItem,
  OverviewResp,
  ScanStatus,
  SectorFlowResp,
  SetupStatus,
  StrategyProfileState,
  TodayGuide,
} from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import SectorFlowPanel from '../components/SectorFlowPanel'
import MoneyHeatmap from '../components/MoneyHeatmap'
import { IcoFlame, IcoLayers, IcoScan, IcoShield, IcoStop, IcoTarget, IcoWallet } from '../components/Icons'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'
import {
  loadOverviewCache,
  loadParams,
  loadPoolPref,
  saveOverviewCache,
  saveParams,
} from '../scanCache'
import type { EChartsOption } from 'echarts'

function tierBadge(tier?: string, pool?: string, tradeable?: boolean) {
  if (pool === 'A' && (tradeable || tier === 'strict')) return { text: '可交易', cls: 'pill ok' }
  if (pool === 'A') return { text: 'A 池', cls: 'pill ok' }
  const t = (tier || '').toLowerCase()
  if (t === 'relaxed') return { text: '放宽观察', cls: 'pill warn' }
  if (t.includes('theme') || t === 'theme_fill') return { text: '主题观察', cls: 'pill' }
  if (t === 'unknown') return { text: '旧数据', cls: 'pill warn' }
  return { text: pool === 'B' ? '观察' : (tier || '—'), cls: 'pill' }
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
  const [classification, setClassification] = useState<ClassificationKey>('industry')
  const [classificationCatalog, setClassificationCatalog] = useState<ClassificationCatalogResp | null>(null)
  const [heatmap, setHeatmap] = useState<MoneyHeatmapResp | null>(null)
  const [heatErr, setHeatErr] = useState('')
  const [todayGuide, setTodayGuide] = useState<TodayGuide | null>(null)
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
  const [profileState, setProfileState] = useState<StrategyProfileState | null>(null)
  const [profileErr, setProfileErr] = useState('')
  const [topN, setTopN] = useState(prefParams?.topN ?? 20)
  const [days, setDays] = useState(prefParams?.days ?? 160)
  const c = useChartColors()
  /** 扫描 API 尚未返回 task_id 时用户已点取消 */
  const pendingCancelRef = useRef(false)

  // A5 竞态守卫：序号 + AbortController，保证只有最新一次请求用于渲染
  const overviewSeq = useRef(0)
  const overviewReqRef = useRef<AbortController | null>(null)
  const sectorReqRef = useRef<AbortController | null>(null)
  const heatmapReqRef = useRef<AbortController | null>(null)

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
    api.sectorFlow(sectorDays, classification, { signal: ac.signal })
      .then(setSector)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === 'AbortError') return
        setSector(null)
      })
  }, [classification, sectorDays])

  // 最新交易日资金热力图（挂载 + 扫描完成后刷新）
  const loadHeatmap = useCallback(() => {
    heatmapReqRef.current?.abort()
    const ac = new AbortController()
    heatmapReqRef.current = ac
    api.moneyHeatmap(10, classification, { signal: ac.signal })
      .then((h) => { setHeatmap(h); setHeatErr('') })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === 'AbortError') return
        setHeatErr(String(e))
      })
  }, [classification])

  useEffect(() => { loadHeatmap() }, [loadHeatmap])

  useEffect(() => {
    api.classifications().then(setClassificationCatalog).catch(() => setClassificationCatalog(null))
  }, [])

  useEffect(() => {
    api.today().then(setTodayGuide).catch(() => setTodayGuide(null))
  }, [])

  useEffect(() => {
    api.backtestProfile()
      .then((next) => { setProfileState(next); setProfileErr('') })
      .catch((reason: unknown) => setProfileErr(reason instanceof Error ? reason.message : String(reason)))
  }, [])

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

  // 顶栏手动更新行情完成后：刷新总览与热力图
  useEffect(() => {
    const onSynced = () => {
      setCacheNote('行情已更新，正在刷新列表…')
      loadOverview({ keepOnFail: false })
      loadHeatmap()
    }
    window.addEventListener('data-synced', onSynced)
    return () => window.removeEventListener('data-synced', onSynced)
  }, [loadOverview, loadHeatmap])

  useEffect(() => {
    return () => {
      overviewReqRef.current?.abort()
      sectorReqRef.current?.abort()
      heatmapReqRef.current?.abort()
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
      setCacheNote(`扫描已启动，参数版本 ${resp.config_hash} 已冻结；切换参数只影响下一次扫描`)
      window.dispatchEvent(new Event(RUN_TASK_EVENT))
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

  const onResetProfile = async () => {
    if (!profileState || profileState.active.is_default) return
    if (!window.confirm('恢复系统默认参数？已完成扫描的历史证据不会删除，当前运行中的扫描也不会被改变。')) return
    setProfileErr('')
    try {
      const next = await api.resetBacktestProfile()
      setProfileState(next)
      setCacheNote('已恢复系统默认参数；下一次扫描生效')
    } catch (reason) {
      setProfileErr(reason instanceof Error ? reason.message : String(reason))
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

  const onTodayAction = () => {
    if (!todayGuide) return
    if (todayGuide.next_action === 'RUN_SCAN') {
      void onScan()
      return
    }
    if (todayGuide.next_action === 'WAIT_SCAN') {
      document.getElementById('scan-controls')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    if (todayGuide.href) {
      nav(todayGuide.href)
      return
    }
    window.alert('请双击项目目录里的“一键启动.bat”。系统会自动同步行情，完成后重新打开本页。')
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
  const tradeableCount = data.items.filter((x) => x.tradeable).length

  return (
    <div className="fade-up">
      {/* 服务端只给出一个正确的今日动作 */}
      <div className="today-card">
        <div className="step-no">1</div>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ color: 'var(--faint)', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 2 }}>
            今日唯一动作 · Today's Action
          </div>
          <div className="t-title">{todayGuide?.title || '正在判断今日状态…'}</div>
          <div className="t-reason">{todayGuide?.reason || '系统正在核对行情、扫描、订单与对账。'}</div>
          {cacheNote && <div className="t-note">{cacheNote}</div>}
        </div>
        {todayGuide && (
          <button type="button" className="btn btn-primary" style={{ padding: '10px 22px', fontSize: 14 }} onClick={onTodayAction}>
            {todayGuide.primary_label}
          </button>
        )}
      </div>

      {/* 状态条：数据新鲜度 + 市场环境 */}
      <div className={`status-bar section-gap ${stale ? 'status-stale' : 'status-ok'}`} style={{ marginTop: 14 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: 'var(--faint)' }}>数据</span>
          <b className="num">{data.as_of || fresh?.as_of || '—'}</b>
          {fresh && (
            <span className={`badge ${stale ? 'badge-danger' : fresh.stale_days > 0 ? 'badge-warn' : 'badge-ok'}`}>
              {fresh.label}
              {fresh.stale_label
                ? ` · ${fresh.stale_label}`
                : ` · 滞后 ${fresh.stale_days} 个${fresh.unit === 'trading' ? '交易日' : '日历日'}`}
            </span>
          )}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: 'var(--faint)' }}>环境</span>
          <b className={regime?.regime === 'defense' ? 'text-danger' : regime?.regime === 'attack' ? 'text-ok' : ''}>
            {regime?.label || '—'}
          </b>
          {regime?.allow_new_entries === false && <span className="badge badge-danger">禁止新开仓</span>}
        </span>
        <span className="muted" style={{ fontSize: 12 }}>A 池 = 可交易 strict · B 池 = 观察（不混排）</span>
      </div>

      {/* KPI 指标带 */}
      <div className="metrics">
        <div className="kpi accent-top">
          <div className="kpi-label">当前池</div>
          <div className="kpi-value">{pool}<span className="kpi-sub"> / {data.count} 只</span></div>
          <div className="kpi-ico"><IcoLayers size={16} /></div>
        </div>
        <div className="kpi accent-top">
          <div className="kpi-label">平均综合分</div>
          <div className="kpi-value">{avgScore.toFixed(1)}</div>
          <div className="kpi-ico"><IcoTarget size={16} /></div>
        </div>
        <div className="kpi accent-top">
          <div className="kpi-label">合计主力净流入</div>
          <div className="kpi-value">{(totalFlow / 10000).toFixed(2)}<span className="kpi-sub"> 亿</span></div>
          <div className="kpi-ico"><IcoWallet size={16} /></div>
        </div>
        <div className="kpi accent-top">
          <div className="kpi-label">可交易票</div>
          <div className="kpi-value">{tradeableCount}</div>
          <div className="kpi-ico"><IcoFlame size={16} /></div>
        </div>
      </div>

      {/* 扫描控制台 */}
      <div id="scan-controls" className="card" style={{ marginBottom: 16 }}>
        <div className="h-sec" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <IcoScan size={16} />扫描控制台
          </h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div className="seg" role="tablist" aria-label="股票池">
              {(['A', 'B', 'ALL'] as const).map((p) => (
                <button key={p} className={`seg-item ${pool === p ? 'on' : ''}`} onClick={() => setPool(p)}>
                  {p === 'A' ? 'A · 可交易' : p === 'B' ? 'B · 观察' : '全部'}
                </button>
              ))}
            </div>
            {loading && <span className="muted" style={{ fontSize: 12 }}>加载中…</span>}
          </div>
        </div>
        {profileState && (
          <div className={`scan-profile-strip ${profileState.active.is_default ? 'default' : 'custom'}`}>
            <div>
              <span>本次扫描参数</span>
              <b>{profileState.active.is_default ? '系统默认' : '专业回测人工启用'}</b>
              <small>
                横盘 {String(profileState.active.entry.box_min_days)}–{String(profileState.active.entry.box_max_days)} 日 ·
                突破量比 ≥ {String(profileState.active.entry.breakout_vol_ratio)} ·
                <span className="mono"> {profileState.active.config_hash}</span>
              </small>
              <small>{profileState.boundary.notice}</small>
            </div>
            <div className="scan-profile-actions">
              <button className="btn btn-sm" type="button" onClick={() => nav('/backtest')}>去回测与选择参数</button>
              {!profileState.active.is_default && (
                <button className="btn btn-sm" type="button" onClick={onResetProfile} disabled={scanning}>恢复系统默认</button>
              )}
            </div>
          </div>
        )}
        {profileErr && <div className="err" style={{ marginBottom: 10 }}>参数档案读取失败：{profileErr}</div>}
        <div className="row" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <label style={{ whiteSpace: 'nowrap' }}>A 池 Top</label>
            <input type="number" value={topN} min={5} max={30} step={1}
              onChange={(e) => setTopN(Number(e.target.value))}
              style={{ width: 72 }} />
          </div>
          <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <label style={{ whiteSpace: 'nowrap' }}>回看天数</label>
            <input type="number" value={days} min={60} max={400} step={10}
              onChange={(e) => setDays(Number(e.target.value))}
              style={{ width: 84 }} />
          </div>
          <button className="btn btn-primary" onClick={onScan} disabled={scanning}>
            <IcoScan size={15} />
            {scanning
              ? (scanStatus?.status === 'cancelling' || scanStatus?.cancel_requested
                ? '取消中…'
                : '扫描中…')
              : '开始扫描（A 池优先）'}
          </button>
          {scanning && (
            <button
              className="btn btn-danger"
              onClick={onCancel}
              disabled={scanStatus?.status === 'cancelling' || !!scanStatus?.cancel_requested}
              title={scanTask ? `取消任务 ${scanTask}` : '取消当前扫描'}
            >
              <IcoStop size={14} />
              {scanStatus?.status === 'cancelling' || scanStatus?.cancel_requested ? '取消中…' : '取消'}
            </button>
          )}
          {defense && (
            <span className="pill danger" style={{ gap: 6 }}><IcoShield size={13} />防守环境：A 池禁止新开仓</span>
          )}
        </div>
        {scanning && scanStatus && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
              <span>
                {scanStatus.stage}
                {(scanStatus.status === 'cancelling' || scanStatus.cancel_requested) && (
                  <span className="badge badge-warn" style={{ marginLeft: 8 }}>取消请求已发送</span>
                )}
              </span>
              <span className="num">{scanStatus.progress}%</span>
            </div>
            <div className="progress">
              <i style={{
                width: `${Math.max(scanStatus.progress, scanStatus.status === 'cancelling' ? 8 : 0)}%`,
                background: scanStatus.status === 'cancelling' || scanStatus.cancel_requested
                  ? 'linear-gradient(90deg, var(--warn), var(--up))'
                  : undefined,
              }} />
            </div>
            {scanTask && <div style={{ marginTop: 6, fontSize: 11, color: 'var(--faint)' }} className="mono">task {scanTask}</div>}
          </div>
        )}
        {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      {/* 最新交易日资金热力图（treemap） */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="h-sec">
          <div>
            <h2 style={{ margin: 0 }}>市场资金热力图</h2>
            <span className="hint">按{heatmap?.classification_title || '细分行业'}显示，净流入和净流出各 Top 10</span>
          </div>
          {profileState && profileState.active.required_scan_days > days && (
            <span className="hint">该参数需要至少 {profileState.active.required_scan_days} 日，启动时会自动扩展回看窗口</span>
          )}
          <label className="classification-control">
            <span>分类标准</span>
            <select
              value={classification}
              onChange={(event) => {
                setClassification(event.target.value as ClassificationKey)
                setHeatmap(null)
                setSector(null)
              }}
              aria-label="资金板块分类标准"
            >
              {(classificationCatalog?.items || [
                { key: 'industry', title: '细分行业', available: true },
                { key: 'market', title: '上市板块', available: true },
                { key: 'area', title: '地域', available: true },
              ]).filter((item) => item.available).map((item) => (
                <option key={item.key} value={item.key}>{item.title}</option>
              ))}
            </select>
          </label>
        </div>
        {heatErr ? (
          <div className="muted" style={{ fontSize: 12 }}>资金热力图不可用：{heatErr}</div>
        ) : heatmap ? (
          <MoneyHeatmap data={heatmap} />
        ) : (
          <div className="muted" style={{ fontSize: 12 }}>加载资金热力图…</div>
        )}
      </div>

      {/* 板块资金流 */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="h-sec">
          <h2 style={{ margin: 0 }}>{sector?.classification_title || '细分行业'}资金流 <span className="tag">观察建仓 / 出逃</span></h2>
          <div className="seg">
            {[5, 10, 20].map((n) => (
              <button key={n} className={`seg-item ${sectorDays === n ? 'on' : ''}`} onClick={() => setSectorDays(n)}>{n} 日</button>
            ))}
          </div>
        </div>
        {sector && <SectorFlowPanel data={sector} />}
      </div>

      {/* 股票卡片网格 */}
      <div className="h-sec" style={{ marginTop: 4 }}>
        <h2 style={{ margin: 0 }}>
          {pool === 'A' ? 'A 池 · 可交易候选' : pool === 'B' ? 'B 池 · 观察名单' : '全部候选'}
          <span className="tag">{data.items.length} 只</span>
        </h2>
      </div>
      <div className="stock-grid">
        {data.items.map((it) => {
          const b = tierBadge(it.tier, it.pool, it.tradeable)
          return (
            <article key={it.ts_code} className="stock-card">
              <div className="head">
                <div>
                  <span className="name">{it.name}</span>
                  <span className="code">{it.code}</span>
                  <span className={b.cls} style={{ marginLeft: 7 }}>{b.text}</span>
                </div>
                <span className="score">{it.score.toFixed(1)}<small>分</small></span>
              </div>
              <div className="meta">
                <span>价 <b className="num">{it.price?.toFixed(2) ?? 'n/a'}</b></span>
                <span>{it.industry}</span>
                <span>市值 <b className="num">{it.mv_yi?.toFixed(0) ?? 'n/a'}亿</b></span>
                <span>量比 <b className="num">{it.vol_ratio?.toFixed(1) ?? 'n/a'}×</b></span>
              </div>
              {it.trade && (
                <div className="trade-row">
                  <span>止损<b className="text-danger num">{it.trade.stop_loss ?? '—'}</b></span>
                  <span>目标1<b className="text-ok num">{it.trade.target_1 ?? '—'}</b></span>
                  <span>仓位<b className="num">{it.trade.position_pct}%</b></span>
                  <span>持有≤<b className="num">{it.trade.max_hold_days}日</b></span>
                </div>
              )}
              {miniOption(it) && <EChart option={miniOption(it)!} height={110} />}
              <div className="reason">
                {it.reasons.split('；').filter(Boolean).slice(0, 4).map((r, i) => (
                  <span key={i}>{i === 0 ? <b>✓ </b> : <span className="sep">· </span>}{r}</span>
                ))}
              </div>
              <div className="stock-card-actions">
                <button className="btn btn-sm" type="button" onClick={() => nav(`/stock/${it.ts_code}`)}>查看详情</button>
                <button className="btn btn-sm" type="button" onClick={() => nav(`/stock/${it.ts_code}#ai-review`)}>AI 评测</button>
              </div>
            </article>
          )
        })}
      </div>
      {!data.items.length && (
        <div className="empty">
          <strong>当前池无标的</strong>
          {regime?.allow_new_entries === false ? '防守环境已禁止新开仓，属正常状态。' : '请运行扫描或切换到 B 观察池。'}
        </div>
      )}
    </div>
  )
}
