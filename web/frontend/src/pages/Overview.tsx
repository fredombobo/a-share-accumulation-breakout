import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import {
  api,
  type ClassificationCatalogResp,
  type ClassificationKey,
  HealthResp,
  MoneyHeatmapResp,
  EntryParameters,
  OverviewItem,
  OverviewResp,
  ScanStatus,
  SectorFlowResp,
  SetupStatus,
  StrategyProfileState,
  TodayGuide,
  ScanHistoryItem,
} from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import SectorFlowPanel from '../components/SectorFlowPanel'
import MoneyHeatmap from '../components/MoneyHeatmap'
import { IcoScan, IcoStop } from '../components/Icons'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'
import {
  loadOverviewCache,
  loadParams,
  loadPoolPref,
  saveOverviewCache,
  saveParams,
} from '../scanCache'
import type { EChartsOption } from 'echarts'
import './Overview.css'

function displayDate(value?: string) {
  if (!value) return '—'
  return /^\d{8}$/.test(value) ? `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6, 8)}` : value.replaceAll('-', '.')
}

function displayTimestamp(value?: string) {
  if (!value || !Number.isFinite(Date.parse(value))) return '未知时间'
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value))
}

function tierBadge(tier?: string, pool?: string, tradeable?: boolean) {
  if (pool === 'A' && (tradeable || tier === 'strict')) return { text: '严格候选', cls: 'pill ok' }
  if (pool === 'A') return { text: 'A 池', cls: 'pill ok' }
  const t = (tier || '').toLowerCase()
  if (t === 'relaxed') return { text: '放宽观察', cls: 'pill warn' }
  if (t === 'data_incomplete') return { text: '数据不足', cls: 'pill warn' }
  if (t === 'strict' && pool === 'B') return { text: '严格形态 · 暂观察', cls: 'pill warn' }
  if (t.includes('theme') || t === 'theme_fill') return { text: '主题观察', cls: 'pill' }
  if (t === 'unknown') return { text: '旧数据', cls: 'pill warn' }
  return { text: pool === 'B' ? '观察' : (tier || '—'), cls: 'pill' }
}

function manualParametersFromProfile(state: StrategyProfileState): EntryParameters {
  const entry = state.active.entry
  return {
    box_min_days: Number(entry.box_min_days),
    box_max_days: Number(entry.box_max_days),
    box_max_amp: Number(entry.box_max_amp),
    breakout_vol_ratio: Number(entry.breakout_vol_ratio),
    breakout_chg_min: Number(entry.breakout_chg_min),
    breakout_chg_max: Number(entry.breakout_chg_max),
    breakout_vs_recent_vol_ratio: Number(entry.breakout_vs_recent_vol_ratio),
    breakout_window_days: Number(entry.breakout_window_days),
    require_structure: Boolean(entry.require_structure),
  }
}

function profileSourceLabel(state: StrategyProfileState): string {
  if (state.active.is_default) return '系统默认'
  if (state.active.source.kind === 'MANUAL_RESEARCH') return '用户手工输入（未回测验证）'
  if (state.active.source.kind === 'BACKTEST_ENTRY_COPY') return '复制的入场条件 · 待验证'
  return '回测结果 · 人工启用'
}

function percentInputValue(value: number): number {
  return Number((value * 100).toFixed(8))
}

export default function Overview() {
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedRun = searchParams.get('run_id') || ''
  const [history, setHistory] = useState<ScanHistoryItem[]>([])
  const [historyErr, setHistoryErr] = useState('')
  const cached = loadOverviewCache()
  const prefPool = loadPoolPref()
  const prefParams = loadParams()
  const initialPool = prefPool || cached?.pool || 'A'
  const initialCache = cached?.pool === initialPool && cached.data.pool === initialPool ? cached : null
  const [data, setData] = useState<OverviewResp | null>(initialCache?.data ?? null)
  const [historicalResult, setHistoricalResult] = useState(!!initialCache)
  const [health, setHealth] = useState<HealthResp | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [sector, setSector] = useState<SectorFlowResp | null>(null)
  const [sectorDays, setSectorDays] = useState(10)
  const [classification, setClassification] = useState<ClassificationKey>('industry')
  const [classificationCatalog, setClassificationCatalog] = useState<ClassificationCatalogResp | null>(null)
  const [heatmap, setHeatmap] = useState<MoneyHeatmapResp | null>(null)
  const [heatErr, setHeatErr] = useState('')
  const [todayGuide, setTodayGuide] = useState<TodayGuide | null>(null)
  const [pool, setPool] = useState<'A' | 'B' | 'ALL'>(initialPool)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [cacheNote, setCacheNote] = useState(
    initialCache
      ? `历史缓存 · 保存于 ${displayTimestamp(initialCache.savedAt)}，正在核对服务端结果`
      : '',
  )
  const [scanning, setScanning] = useState(false)
  const [scanTask, setScanTask] = useState<string | null>(null)
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null)
  const [profileState, setProfileState] = useState<StrategyProfileState | null>(null)
  const [profileErr, setProfileErr] = useState('')
  const [manualProfileOpen, setManualProfileOpen] = useState(false)
  const [manualProfile, setManualProfile] = useState<EntryParameters | null>(null)
  const [editConfigHash, setEditConfigHash] = useState('')
  const [syncStarting, setSyncStarting] = useState(false)
  const [manualSaving, setManualSaving] = useState(false)
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
    setData((current) => current?.pool === pool && (!selectedRun || current.publication?.run_id === selectedRun) ? current : null)
    setErr('')
    api.health({ signal }).then(setHealth).catch(() => setHealth(null))
    api.setupStatus({ signal }).then(setSetup).catch(() => setSetup(null))
    api.overview(pool, { signal, timeoutMs: 60_000 }, selectedRun || undefined)
      .then((resp) => {
        if (overviewSeq.current !== seq) return
        if (resp.pool && resp.pool !== pool) throw new Error('结果股票池与请求不一致，请重新读取')
        const current = { ...resp, pool }
        setLoading(false)
        setData(current)
        // 空集也是一次有效响应，必须覆盖旧缓存。
        if (!selectedRun) saveOverviewCache(pool, current)
        setHistoricalResult(false)
        setCacheNote('')
        setErr('')
      })
      .catch((e: unknown) => {
        if (overviewSeq.current !== seq) return // 切池等新请求已接管，忽略旧失败
        if (signal.aborted) return // 外部取消；内部请求超时仍须显示错误
        setLoading(false)
        const isAbort = (e as { name?: string })?.name === 'AbortError'
        setErr(isAbort ? '读取结果超时，请稍后重试。' : `读取结果失败：${e instanceof Error ? e.message : String(e)}`)
        if (keepOnFail && !selectedRun) {
          const c2 = loadOverviewCache()
          if (c2?.pool === pool && c2.data.pool === pool) {
            setData(c2.data)
            setHistoricalResult(true)
            setCacheNote(`仅显示 ${pool} 池历史缓存 · 保存于 ${displayTimestamp(c2.savedAt)}，尚未核对最新结果`)
            return
          }
        }
        setData(null)
        setHistoricalResult(false)
        setCacheNote('')
      })
  }, [pool, selectedRun])

  const loadHistory = useCallback(() => {
    api.scanRuns().then((response) => { setHistory(response.runs.filter((run) => run.status === 'SUCCEEDED')); setHistoryErr('') })
      .catch(() => setHistoryErr('历史记录暂不可用'))
  }, [])
  useEffect(() => { loadHistory() }, [loadHistory])

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
      .then((next) => {
        if (!next?.active?.entry || !next?.boundary) throw new Error('参数档案接口返回不完整')
        setProfileState(next)
        setManualProfile(manualParametersFromProfile(next))
        setEditConfigHash(next.active.config_hash)
        setProfileErr('')
      })
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
        loadHistory()
        api.today().then(setTodayGuide).catch(() => undefined)
        setCacheNote('扫描已完成，正在读取本次结果…')
        loadOverview({ keepOnFail: false })
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
          // Only the server can confirm cancellation. A slow cancellation is
          // still running, so keep its identity and continue polling.
          if (Date.now() - cancelSince > 15000) {
            interval = 2000
            setCacheNote('取消请求已送达，正在等待服务端确认；任务仍受跟踪。')
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
  }, [scanning, scanTask, loadOverview, loadHistory])

  const onScan = async () => {
    if (selectedRun) setSearchParams({})
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
    setProfileErr('')
    try {
      const next = await api.resetBacktestProfile()
      setProfileState(next)
      setManualProfile(manualParametersFromProfile(next))
      setEditConfigHash(next.active.config_hash)
      setCacheNote('已恢复系统默认参数；下一次扫描生效')
    } catch (reason) {
      setProfileErr(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const updateManualNumber = (key: keyof EntryParameters, raw: string, percentage = false) => {
    const value = Number(raw)
    setManualProfile((current) => current ? { ...current, [key]: percentage ? value / 100 : value } : current)
  }

  const onSaveManualProfile = async () => {
    if (!manualProfile || scanning || !editConfigHash) return
    setManualSaving(true)
    setProfileErr('')
    try {
      const next = await api.saveEntryProfile(manualProfile, editConfigHash)
      setProfileState(next)
      setManualProfile(manualParametersFromProfile(next))
      setEditConfigHash(next.active.config_hash)
      setManualProfileOpen(false)
      setCacheNote('已保存手工研究参数；下一次扫描会冻结这组参数')
    } catch (reason) {
      setProfileErr(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setManualSaving(false)
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

  const onTodayAction = async () => {
    if (!todayGuide) return
    if (todayGuide.next_action === 'RUN_SCAN') {
      void onScan()
      return
    }
    if (todayGuide.next_action === 'WAIT_SCAN') {
      document.getElementById('scan-controls')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    if (todayGuide.next_action === 'SYNC_DATA') {
      setSyncStarting(true)
      try {
        await api.syncStart()
        window.dispatchEvent(new Event(RUN_TASK_EVENT))
        setCacheNote('数据更新已启动，可在顶部查看进度；完成后再扫描。')
      } catch (error) { setErr(error instanceof Error ? error.message : String(error)) }
      finally { setSyncStarting(false) }
      return
    }
    if (todayGuide.href) {
      nav(todayGuide.href)
      return
    }
    nav('/guide')
  }

  const miniOption = (it: OverviewItem): EChartsOption | null => {
    const k = it.kline || []
    if (!k.length) return null
    const cat = k.map((d) => d.trade_date)
    const candle = k.map((d) => [d.open, d.close, d.low, d.high])
    const boxHigh = it.box_high
    const boxLow = it.box_low
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
            ]
          : []),
      ],
    }
  }

  const visibleData = data?.pool === pool && (!selectedRun || data.publication?.run_id === selectedRun) ? data : null
  const items = visibleData?.items || []
  const avgScore = items.length ? items.reduce((sum, item) => sum + item.score, 0) / items.length : 0
  const availableFlows = items.map((item) => item.fund_net_wan).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const totalFlow = availableFlows.length ? availableFlows.reduce((sum, value) => sum + value, 0) : null
  const fresh = visibleData?.freshness || health?.freshness
  const regime = visibleData?.regime || health?.regime
  const stale = !!fresh?.is_stale
  const defense = regime?.allow_new_entries === false
  const cancelling = scanStatus?.status === 'cancelling' || !!scanStatus?.cancel_requested
  const publication = visibleData?.publication
  const isHistory = !!selectedRun || visibleData?.view_state === 'HISTORICAL'
  const entryChanged = !!(publication?.entry_hash && profileState?.active.entry_hash && publication.entry_hash !== profileState.active.entry_hash)
  const completeCount = publication?.qualification?.total ?? publication?.qualification?.counts?.total
    ?? (publication?.qualification?.counts ? publication.qualification.counts.A + publication.qualification.counts.B : null)
  const stockHref = (code: string) => `/stock/${code}${isHistory && publication ? `?run_id=${encodeURIComponent(publication.run_id)}` : ''}`

  return (
    <div className="overview-workspace fade-up">
      <header className="overview-heading">
        <div>
          <span className="overview-eyebrow">MARKET RESEARCH / DAILY</span>
          <h1>每日选股</h1>
          <p>从市场中发现值得进一步研究的突破形态。</p>
        </div>
        <div className="overview-heading-actions">
          <button className="btn" type="button" onClick={() => loadOverview()} disabled={loading}>{loading ? '正在读取…' : '刷新结果'}</button>
          <button className="btn" type="button" onClick={() => nav('/backtest')}>回测研究 <span aria-hidden="true">↗</span></button>
        </div>
      </header>

      <div className="overview-market-status" aria-label="市场与数据状态">
        <span><i className={`overview-status-dot ${stale ? 'is-stale' : ''}`} />{fresh?.label || '正在核对数据'}</span>
        <span>行情日期 <b className="num">{displayDate(health?.as_of || fresh?.as_of)}</b></span>
        <span>市场环境 <b>{regime?.label || '—'}</b></span>
        {stale && <span className="overview-status-warning">数据待更新 · 结果仅供历史研究</span>}
        {defense && <span className="overview-status-warning">防守环境</span>}
      </div>
      <details className="overview-data-audit">
        <summary>数据核对明细 <span>应完成交易日 {displayDate(fresh?.expected_as_of)}</span></summary>
        <div>
          <span>独立交易日历 <b>{fresh?.calendar_verified ? '已核对' : '未认证'}</b></span>
          {Object.entries(fresh?.dataset_freshness || {}).map(([key, value]) => <span key={key}>{key === 'moneyflow' ? '资金流' : '每日指标'} <b>{displayDate(value.latest_date)} · {value.is_current ? '已到齐' : '待更新'}</b></span>)}
          <span>资金观察窗口 <b>{fresh?.required_moneyflow_dates?.map(displayDate).join(' / ') || '待核对'}</b></span>
        </div>
        {fresh?.blocking_reasons?.length ? <p>当前受限：{fresh.blocking_reasons.join(' / ')}。全局到齐后，仍会逐股核对缺失数据。</p> : <p>全局日期通过只表示数据表已更新，个股完整性在候选证据中单独核对。</p>}
      </details>

      <section id="scan-controls" className="overview-config" aria-labelledby="overview-config-title">
        <div className="overview-section-heading">
          <div>
            <span className="overview-eyebrow">NEXT SCAN</span>
            <h2 id="overview-config-title">下一次扫描设置</h2>
          </div>
          {profileState && <span className="overview-source">{profileSourceLabel(profileState)}</span>}
        </div>
        <div className="overview-config-main">
          <div className="overview-entry-summary">
            {profileState ? (
              <>
                <span>横盘 <b>{String(profileState.active.entry.box_min_days)}–{String(profileState.active.entry.box_max_days)} 日</b></span>
                <span>箱体振幅 <b>≤ {percentInputValue(Number(profileState.active.entry.box_max_amp))}%</b></span>
                <span>突破量比 <b>≥ {String(profileState.active.entry.breakout_vol_ratio)}</b></span>
              </>
            ) : <span>{profileErr ? '参数档案暂不可用' : '正在读取筛选条件…'}</span>}
            <button className="overview-text-button" type="button" onClick={() => setManualProfileOpen((value) => !value)} disabled={scanning || !manualProfile} aria-expanded={manualProfileOpen}>
              {manualProfileOpen ? '收起条件' : '编辑筛选条件'} <span aria-hidden="true">{manualProfileOpen ? '−' : '+'}</span>
            </button>
          </div>
          <div className="overview-scan-actions">
            <label>A 池展示上限<input aria-label="A 池展示上限" type="number" min={5} max={30} step={1} value={topN} onChange={(event) => setTopN(Number(event.target.value))} /></label>
            <label>回看天数<input aria-label="回看天数" type="number" min={60} max={400} step={10} value={days} onChange={(event) => setDays(Number(event.target.value))} /></label>
            <button className="btn btn-primary" type="button" onClick={onScan} disabled={scanning}><IcoScan size={15} />{scanning ? (cancelling ? '取消中…' : '扫描中…') : '开始扫描'}</button>
            {scanning && <button className="btn btn-danger" type="button" onClick={onCancel} disabled={cancelling}><IcoStop size={14} />取消扫描</button>}
          </div>
        </div>
        <p className="overview-config-note">条件用于严格形态筛选，观察补量沿用固定规则。展示上限不改变完整资格名单；新设置在下一次扫描生效。{profileState && profileState.active.required_scan_days > days ? ` 本档案需要至少 ${profileState.active.required_scan_days} 日，扫描时自动扩展。` : ''}</p>
        {entryChanged && <p className="overview-entry-changed">下一次条件已改变。本次结果仍保留扫描时的条件与证据。</p>}
        {manualProfileOpen && manualProfile && (
          <section className="overview-entry-editor" aria-label="下一次扫描筛选条件">
            <div className="overview-editor-heading"><h3>入选条件</h3><span>手工调整会标记为未回测验证</span></div>
            <div className="overview-fields">
              <label>横盘最短（交易日）<input type="number" min="20" max="200" value={manualProfile.box_min_days} onChange={(event) => updateManualNumber('box_min_days', event.target.value)} /></label>
              <label>横盘最长（交易日）<input type="number" min="40" max="240" value={manualProfile.box_max_days} onChange={(event) => updateManualNumber('box_max_days', event.target.value)} /></label>
              <label>箱体最大振幅（%）<input type="number" min="5" max="60" step="0.5" value={percentInputValue(manualProfile.box_max_amp)} onChange={(event) => updateManualNumber('box_max_amp', event.target.value, true)} /></label>
              <label>突破量 / 箱体均量<input type="number" min="1" max="5" step="0.1" value={manualProfile.breakout_vol_ratio} onChange={(event) => updateManualNumber('breakout_vol_ratio', event.target.value)} /></label>
              <label>突破最小涨幅（%）<input type="number" min="0.1" max="15" step="0.1" value={percentInputValue(manualProfile.breakout_chg_min)} onChange={(event) => updateManualNumber('breakout_chg_min', event.target.value, true)} /></label>
              <label>突破最大涨幅（%）<input type="number" min="1" max="30" step="0.1" value={percentInputValue(manualProfile.breakout_chg_max)} onChange={(event) => updateManualNumber('breakout_chg_max', event.target.value, true)} /></label>
              <label>突破量 / 前 5 日均量<input type="number" min="0.8" max="5" step="0.1" value={manualProfile.breakout_vs_recent_vol_ratio} onChange={(event) => updateManualNumber('breakout_vs_recent_vol_ratio', event.target.value)} /></label>
              <label>近期突破观察窗（日）<input type="number" min="1" max="20" value={manualProfile.breakout_window_days} onChange={(event) => updateManualNumber('breakout_window_days', event.target.value)} /></label>
              <label className="overview-checkbox"><input type="checkbox" checked={manualProfile.require_structure} onChange={(event) => setManualProfile((current) => current ? { ...current, require_structure: event.target.checked } : current)} />要求完整吸筹结构</label>
            </div>
            <div className="overview-editor-footer">
              <span>只保存九项筛选条件；止盈、止损与持有期由回测研究管理。</span>
              <div><button className="btn" type="button" onClick={() => { setManualProfileOpen(false); if (profileState) setManualProfile(manualParametersFromProfile(profileState)) }}>取消</button><button className="btn btn-primary" type="button" onClick={onSaveManualProfile} disabled={manualSaving || scanning}>{manualSaving ? '保存中…' : '保存筛选条件'}</button></div>
            </div>
            {profileState && !profileState.active.is_default && <button className="overview-text-button" type="button" onClick={onResetProfile} disabled={scanning}>恢复系统默认档案</button>}
          </section>
        )}
        {profileErr && <div className="overview-notice is-error" role="alert"><span>参数档案：{profileErr}</span><button className="overview-text-button" type="button" onClick={() => { api.backtestProfile().then(next => { setProfileState(next); setManualProfile(manualParametersFromProfile(next)); setEditConfigHash(next.active.config_hash); setProfileErr('') }).catch(() => setProfileErr('参数载入失败，请稍后重试')) }}>重新载入条件</button></div>}
        {scanning && scanStatus && (
          <div className="overview-scan-progress" aria-live="polite">
            <div><span>{scanStatus.stage}</span><b className="num">{scanStatus.progress}%</b></div>
            <div className="overview-progress-track" role="progressbar" aria-valuenow={scanStatus.progress} aria-valuemin={0} aria-valuemax={100} aria-label="扫描进度"><i style={{ width: `${Math.max(0, Math.min(100, scanStatus.progress))}%` }} /></div>
          </div>
        )}
      </section>

      {todayGuide && todayGuide.next_action !== 'RUN_SCAN' && (
        <div className="overview-today"><div><b>{todayGuide.title}</b><span>{todayGuide.reason}</span></div><button className="overview-text-button" type="button" onClick={onTodayAction} disabled={syncStarting}>{syncStarting ? '正在启动…' : todayGuide.primary_label} <span aria-hidden="true">→</span></button></div>
      )}

      <section className="overview-results" aria-labelledby="overview-results-title" aria-busy={loading}>
        <div className="overview-results-heading">
          <div><span className="overview-eyebrow">SCAN RESULTS</span><h2 id="overview-results-title">{selectedRun ? '历史结果' : '本次结果'} <span className="overview-count">{visibleData ? items.length : '—'}</span></h2></div>
          <div className="overview-pool-tabs" role="tablist" aria-label="股票池">
            {(['A', 'B', 'ALL'] as const).map((value) => <button key={value} type="button" role="tab" aria-selected={pool === value} className={pool === value ? 'is-active' : ''} onClick={() => setPool(value)}>{value === 'A' ? 'A · 严格候选' : value === 'B' ? 'B · 观察名单' : '全部'}</button>)}
          </div>
        </div>
        <div className="overview-result-identity">
          <div><span className={`overview-result-state ${isHistory || historicalResult ? 'is-history' : visibleData?.is_current ? 'is-current' : ''}`}>{historicalResult ? '缓存待核对' : selectedRun ? '历史查看' : visibleData?.is_current ? '已发布' : visibleData?.view_state === 'HISTORICAL' ? '当前不可用' : '待发布'}</span><span>{publication ? `记录 ${publication.run_id}` : '等待首次成功扫描'}</span>{publication?.completed_at && <time>{publication.completed_at.slice(0, 19).replace('T', ' ')}</time>}</div>
          <label>查看记录<select aria-label="查看扫描记录" value={selectedRun} onChange={(event) => { setData(null); setSearchParams(event.target.value ? { run_id: event.target.value } : {}) }}><option value="">最新发布</option>{history.map(run => <option value={run.run_id} key={run.run_id}>{displayDate(run.as_of)} · {run.created_at.slice(11, 16)} · {run.run_id.slice(0, 12)}</option>)}</select></label>
        </div>
        {historyErr && <p className="overview-config-note">{historyErr}</p>}
        {selectedRun && <div className="overview-notice is-history"><span>正在查看冻结的历史名单。走势截至扫描日，参数和身份按当次记录解释。</span><button className="overview-text-button" type="button" onClick={() => { setData(null); setSearchParams({}) }}>返回最新</button></div>}
        {publication && <div className="overview-result-proof">
          <span>本次条件 <b className="num" title={publication.entry_hash}>{publication.entry_hash?.slice(0, 12) || '未记录'}</b></span>
          <span>完整资格 <b>{publication.qualification_integrity_error ? '核对未通过' : completeCount ?? '旧记录未归档'}</b></span>
          <span>展示 A / B <b>{publication.counts ? `${publication.counts.A} / ${publication.counts.B}` : '未记录'}</b></span>
          {typeof publication.pool_report?.withheld_strict === 'number' && publication.pool_report.withheld_strict > 0 && <span>严格形态转观察 <b>{publication.pool_report.withheld_strict} 只</b></span>}
        </div>}
        {publication?.qualification_integrity_error && <div className="overview-notice is-error" role="alert">完整名单的数量、哈希或证据未通过一致性核对。请重新扫描，当前记录不可作为有效候选。</div>}
        {defense && publication && <p className="overview-gate-note">本次市场环境处于防守期，严格形态会进入观察名单；A 池为零不表示扫描失败。</p>}
        <div className="overview-results-meta">
          <span>扫描基准日 <b className="num">{displayDate(visibleData?.as_of)}</b></span>
          {historicalResult && visibleData && <span className="overview-history-label">历史缓存 · 待核对</span>}
          {items.length > 0 && <><span>平均综合分 <b className="num">{avgScore.toFixed(1)}</b></span><span>有数据候选资金净额 <b className="num">{totalFlow === null ? '缺少数据' : `${(totalFlow / 10000).toFixed(2)} 亿`}</b> · 已取得 {availableFlows.length}/{items.length} 只</span></>}
          <span className="overview-results-hint">综合分用于相对排序，不是上涨概率</span>
        </div>
        {err && <div className="overview-notice is-error" role="alert"><span>{err}</span><button type="button" className="overview-text-button" onClick={() => loadOverview()} disabled={loading}>重试</button></div>}
        {cacheNote && <div className={`overview-notice ${historicalResult ? 'is-history' : ''}`} role="status">{cacheNote}</div>}
        {loading && !items.length ? (
          <div className="overview-empty" role="status"><span className="overview-loading-line" /><h3>正在读取候选</h3><p>正在核对 {pool === 'ALL' ? '全部股票池' : `${pool} 池`} 的服务端扫描结果。</p></div>
        ) : items.length ? (
          <div className="overview-candidates">
            {items.map((it, index) => {
              const badge = tierBadge(it.tier, it.pool, it.tradeable)
              const chart = miniOption(it)
              const reasons = it.reasons.replace(/^\[[^\]]+\]\s*/, '').split('；').filter(Boolean).slice(0, 3).join(' · ')
              return (
                <article key={it.ts_code} className="overview-candidate" aria-label={`${it.name}候选证据`}>
                  <div className="overview-candidate-identity">
                    <span className="overview-rank num">{String(index + 1).padStart(2, '0')}</span>
                    <div><div className="overview-stock-name"><button type="button" onClick={() => nav(stockHref(it.ts_code))}>{it.name}</button><span className={badge.cls}>{badge.text}</span></div><div className="overview-stock-meta"><span className="num">{it.code}</span><span>{it.industry || '行业未分类'}</span></div><div className="overview-stock-price"><b className="num">{it.price?.toFixed(2) ?? '—'}</b><span>元 · 市值 {it.mv_yi?.toFixed(0) ?? '—'} 亿</span></div></div>
                  </div>
                  <div className="overview-candidate-chart">{chart ? <EChart option={chart} height={82} /> : <div className="overview-chart-empty">暂无走势数据</div>}<span>{selectedRun ? '扫描日以前' : '近期走势'} · 箱体参考</span></div>
                  <div className="overview-candidate-evidence">
                    <dl><div><dt>横盘天数</dt><dd className="num">{it.box_days ?? '—'}<small> 日</small></dd></div><div><dt>箱体振幅</dt><dd className="num">{it.box_amp?.toFixed(1) ?? '—'}<small>%</small></dd></div><div><dt>突破量比</dt><dd className="num">{it.vol_ratio?.toFixed(1) ?? '—'}<small>×</small></dd></div></dl>
                    <p className="overview-evidence-reason" title={reasons}>{reasons || '暂无入选原因摘要，请查看详情核对。'}</p>
                    <span className="overview-breakout-date">突破日 {displayDate(it.breakout_date)}</span>
                    {it.fund_window && <span className={`overview-funding-window ${it.fund_window.complete ? '' : 'is-incomplete'}`} title={it.fund_window.reason}>{it.fund_window.complete ? '资金窗口完整' : '资金窗口待补齐'} · {it.fund_window.observed_days ?? it.fund_window.observed_dates?.length ?? 0}/{it.fund_window.required_days ?? 5} 日{it.data_missing_fields?.length ? ` · 指标缺失 ${it.data_missing_fields.length} 项` : ''}</span>}
                  </div>
                  <div className="overview-candidate-actions"><div className="overview-score"><b className="num">{it.score.toFixed(1)}</b><span>综合分</span></div><button className="overview-text-button" type="button" onClick={() => nav(stockHref(it.ts_code))} aria-label={`查看${it.name}详情`}>查看证据 <span aria-hidden="true">→</span></button>{!isHistory && <button className="overview-ai-button" type="button" onClick={() => nav(`${stockHref(it.ts_code)}#ai-review`)}>AI 评测</button>}</div>
                </article>
              )
            })}
          </div>
        ) : (
          <div className="overview-empty"><span className="overview-empty-symbol" aria-hidden="true">∅</span><h3>{err ? '暂未取得当前池结果' : '当前池暂无候选'}</h3><p>{visibleData?.empty_reason || (err ? '请重试读取结果，或稍后再来查看。' : defense ? '当前处于防守环境，没有严格候选是正常结果。' : '空名单也是筛选结果，可以查看其他股票池或调整下一次筛选条件。')}</p></div>
        )}
      </section>

      <section className="overview-market-context" aria-labelledby="overview-market-title">
        <div className="overview-section-heading"><div><span className="overview-eyebrow">MARKET CONTEXT</span><h2 id="overview-market-title">资金环境</h2><p>结合行业资金分布，补充候选的市场背景。</p></div><label className="overview-classification">分类标准<select value={classification} onChange={(event) => { setClassification(event.target.value as ClassificationKey); setHeatmap(null); setSector(null) }} aria-label="资金板块分类标准">{(classificationCatalog?.items || [{ key: 'industry', title: '细分行业', available: true }, { key: 'market', title: '上市板块', available: true }, { key: 'area', title: '地域', available: true }]).filter((item) => item.available).map((item) => <option key={item.key} value={item.key}>{item.title}</option>)}</select></label></div>
        <div className="overview-market-panel"><div className="overview-panel-heading"><h3>市场资金热力图</h3><span>净流入 / 净流出各 Top 10</span></div>{heatErr ? <div className="overview-notice">资金热力图不可用：{heatErr}</div> : heatmap ? <MoneyHeatmap data={heatmap} /> : <div className="overview-chart-empty">正在读取资金分布…</div>}</div>
        <div className="overview-market-panel"><div className="overview-panel-heading"><h3>{sector?.classification_title || '细分行业'}资金流</h3><div className="overview-period-tabs" aria-label="资金流观察天数">{[5, 10, 20].map((n) => <button type="button" key={n} aria-pressed={sectorDays === n} className={sectorDays === n ? 'is-active' : ''} onClick={() => setSectorDays(n)}>{n} 日</button>)}</div></div>{sector ? <SectorFlowPanel data={sector} /> : <div className="overview-chart-empty">暂无资金流数据</div>}</div>
      </section>
    </div>
  )
}
