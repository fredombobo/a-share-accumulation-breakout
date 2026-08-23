import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'
import type { EChartsOption } from 'echarts'
import {
  api,
  ApiError,
  LabArenaResp,
  LabBoardResp,
  LabCatalog,
  LabMetricRow,
  LabOptimizeBody,
  LabReportHistoryItem,
  LabResearchStatus,
  LabStatusResp,
  LabTrustedReport,
} from '../api/client'
import EChart from '../components/EChart'
import LabTrustedReportView from '../components/LabTrustedReport'
import LabGuided from '../components/lab/LabGuided'
import { useViewMode, ViewModeToggle } from '../components/guidance/BeginnerUi'
import { useChartColors } from '../theme/ThemeContext'

type TabId = 'playbook' | 'console' | 'results'
type RunMode = 'grid' | 'single'
type StratId = 'A' | 'B'
const TERMINAL_TASK_STATES = ['done', 'error', 'cancelled', 'interrupted']

const fmt = (v: unknown, digits = 2) => {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  return n.toFixed(digits)
}
const pct = (v: unknown) => {
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const x = Math.abs(n) <= 1.5 ? n * 100 : n
  return `${x.toFixed(1)}%`
}
const pfClass = (v: unknown) => {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  if (n >= 1.2) return 'pf-good'
  if (n < 1) return 'pf-bad'
  return 'pf-mid'
}
const stopShow = (v: unknown) => {
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const x = Math.abs(n) <= 1.5 ? n * 100 : n
  return `${x.toFixed(1)}%`
}

function comboCount(grid: Record<string, number[]>) {
  const keys = Object.keys(grid)
  if (!keys.length) return 0
  return keys.reduce((acc, k) => acc * Math.max(1, grid[k]?.length || 0), 1)
}

function BoardTable({
  rows,
  emptyHint,
  selectedIdx,
  onSelect,
  oosMode,
}: {
  rows: LabMetricRow[]
  emptyHint: string
  selectedIdx: number | null
  onSelect: (i: number, row: LabMetricRow) => void
  oosMode?: boolean
}) {
  if (!rows.length) {
    return (
      <div className="lab-empty">
        <strong>暂无数据</strong>
        {emptyHint}
      </div>
    )
  }
  return (
    <div className="lab-table-wrap">
      <table className="lab-table">
        <thead>
          <tr>
            <th>#</th>
            <th>方案</th>
            <th className="num">量比</th>
            <th className="num">清零</th>
            <th className="num">窗口</th>
            <th className="num">止损</th>
            <th className="num">净交易</th>
            <th className="num">净胜率</th>
            <th className="num">净PF</th>
            <th className="num">净回撤</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const wr = oosMode ? r.oos_net_win_rate : r.net_win_rate
            const pf = oosMode ? r.oos_net_profit_factor : r.net_profit_factor
            const dd = oosMode ? r.oos_net_max_drawdown : r.net_max_drawdown
            const n = oosMode ? r.oos_net_n_trades : r.net_n_trades
            return (
              <tr
                key={i}
                className={`${i === 0 ? 'rank-1' : ''} ${selectedIdx === i ? 'selected' : ''}`}
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect(i, r)}
              >
                <td>{i + 1}</td>
                <td><span className="badge">{String(r.strategy ?? '—')}</span></td>
                <td className="num">{fmt(r.vol_ratio_min, 2)}</td>
                <td className="num">{fmt(r.strong_reset, 0)}</td>
                <td className="num">{fmt(r.exit_window, 0)}</td>
                <td className="num">{stopShow(r.stop_pct)}</td>
                <td className="num">{fmt(n, 0)}</td>
                <td className="num">{pct(wr)}</td>
                <td className={`num ${pfClass(pf)}`}>{fmt(pf, 3)}</td>
                <td className="num">{pct(dd)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function StrategyLab() {
  const c = useChartColors()
  const location = useLocation()
  const { mode: viewMode, setMode: setViewMode } = useViewMode('lab')
  const [tab, setTab] = useState<TabId>('playbook')
  const [catalog, setCatalog] = useState<LabCatalog | null>(null)
  const [research, setResearch] = useState<LabResearchStatus | null>(null)
  const [isBoard, setIsBoard] = useState<LabBoardResp>({ rows: [], source: '' })
  const [oosBoard, setOosBoard] = useState<LabBoardResp>({ rows: [], source: '' })
  const [arena, setArena] = useState<LabArenaResp | null>(null)
  const [task, setTask] = useState<LabStatusResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [guidedError, setGuidedError] = useState<unknown>(null)
  const [lastResult, setLastResult] = useState<LabStatusResp['result'] | null>(null)
  const [latestReport, setLatestReport] = useState<LabTrustedReport | null>(null)
  const [reportHistory, setReportHistory] = useState<LabReportHistoryItem[]>([])

  // controls
  const [strategy, setStrategy] = useState<StratId>('A')
  const [runMode, setRunMode] = useState<RunMode>('grid')
  const [maxCodes, setMaxCodes] = useState(200)
  const [step, setStep] = useState(10)
  const [useAutoWin, setUseAutoWin] = useState(true)
  const [isStart, setIsStart] = useState('')
  const [isEnd, setIsEnd] = useState('')
  const [oosStart, setOosStart] = useState('')
  const [oosEnd, setOosEnd] = useState('')

  // grid selections (which option chips are on)
  const [gridSel, setGridSel] = useState<Record<string, number[]>>({})
  // single params
  const [single, setSingle] = useState({
    vol_ratio_min: 1.5,
    strong_reset: 3,
    exit_window: 10,
    stop_pct: 0.07,
  })

  const [selected, setSelected] = useState<LabMetricRow | null>(null)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const [selectedKind, setSelectedKind] = useState<'IS' | 'OOS'>('IS')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadBoards = useCallback(() => {
    api.labLeaderboard('IS').then(setIsBoard).catch((e) => setErr(String(e)))
    api.labLeaderboard('OOS').then(setOosBoard).catch(() => undefined)
    api.labArena().then(setArena).catch(() => undefined)
    api.labResearchStatus(false).then((r) => {
      setResearch(r)
      if (r.plan) {
        // 仅在尚未手填时写入推荐窗
        setIsStart((prev) => prev || r.plan.is_start)
        setIsEnd((prev) => prev || r.plan.is_end)
        setOosStart((prev) => prev || r.plan.oos_start)
        setOosEnd((prev) => prev || r.plan.oos_end)
      }
    }).catch(() => undefined)
    api.labReports(20).then((r) => {
      setReportHistory(r.items)
      if (r.items[0]?.research_run_id) {
        api.labReport(r.items[0].research_run_id)
          .then((envelope) => setLatestReport(envelope.report))
          .catch(() => undefined)
      }
    }).catch(() => undefined)
  }, [])

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const acceptTask = useCallback((t: LabStatusResp) => {
    setTask(t)
    const terminal = TERMINAL_TASK_STATES.includes(t.status)
    setBusy(!terminal && t.status !== 'idle')
    if (!terminal) {
      setTab('console')
      return
    }
    stopPoll()
    if (t.status === 'done' && t.result) {
      setLastResult(t.result)
      if (t.result.is_top?.length) {
        setSelected(t.result.is_top[0])
        setSelectedIdx(0)
        setSelectedKind('IS')
      }
      setTab('results')
    } else if (t.status === 'interrupted') {
      setTab('console')
    }
    loadBoards()
  }, [loadBoards, stopPoll])

  const pollTask = useCallback(async (tid?: string) => {
    const t = await api.labStatus(tid)
    acceptTask(t)
    return t
  }, [acceptTask])

  const startPoll = useCallback((tid: string) => {
    stopPoll()
    void pollTask(tid).catch(() => undefined)
    pollRef.current = setInterval(() => {
      void pollTask(tid).catch(() => undefined)
    }, 2500)
  }, [pollTask, stopPoll])

  const restoreTask = useCallback(async () => {
    const restored = await pollTask()
    if (restored.task_id && ['pending', 'running', 'cancelling'].includes(restored.status)) {
      startPoll(restored.task_id)
    }
  }, [pollTask, startPoll])

  useEffect(() => {
    api.health().then((health) => {
      if (health.guided_ui_enabled === false) setViewMode('advanced')
    }).catch(() => undefined)
    api.labCatalog()
      .then((cat) => {
        setCatalog(cat)
        setGridSel({
          vol_ratio_min: [...cat.grid_default.vol_ratio_min],
          strong_reset: [...cat.grid_default.strong_reset],
          exit_window: [...cat.grid_default.exit_window],
          stop_pct: [...cat.grid_default.stop_pct],
        })
        setSingle({
          vol_ratio_min: cat.defaults.vol_ratio_min,
          strong_reset: cat.defaults.strong_reset,
          exit_window: cat.defaults.exit_window,
          stop_pct: cat.defaults.stop_pct,
        })
      })
      .catch((e) => setErr(String(e)))
    loadBoards()
    void restoreTask().catch((e) => setErr(String(e)))

    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') void restoreTask().catch(() => undefined)
    }
    const refreshOnFocus = () => { void restoreTask().catch(() => undefined) }
    document.addEventListener('visibilitychange', refreshWhenVisible)
    window.addEventListener('focus', refreshOnFocus)
    return () => {
      stopPoll()
      document.removeEventListener('visibilitychange', refreshWhenVisible)
      window.removeEventListener('focus', refreshOnFocus)
    }
  }, [loadBoards, restoreTask, setViewMode, stopPoll])

  const nCombos = useMemo(() => comboCount(gridSel), [gridSel])
  const estMin = useMemo(() => {
    const base = Math.ceil((maxCodes / 200) * (10 / step) * 2.2)
    if (runMode === 'single') return Math.max(1, Math.ceil(base * 0.25))
    const full = catalog?.grid_combo_count || 54
    return Math.max(1, Math.ceil(base * Math.max(0.15, nCombos / full)))
  }, [maxCodes, step, runMode, nCombos, catalog])

  const toggleChip = (key: string, val: number) => {
    setGridSel((prev) => {
      const cur = new Set(prev[key] || [])
      if (cur.has(val)) {
        if (cur.size <= 1) return prev // 至少留一个
        cur.delete(val)
      } else cur.add(val)
      return { ...prev, [key]: Array.from(cur).sort((a, b) => a - b) }
    })
  }

  const applyRecommendedWindow = () => {
    if (!research?.plan) return
    setIsStart(research.plan.is_start)
    setIsEnd(research.plan.is_end)
    setOosStart(research.plan.oos_start)
    setOosEnd(research.plan.oos_end)
    setUseAutoWin(true)
  }

  const buildBody = (): LabOptimizeBody => {
    const body: LabOptimizeBody = {
      strategy,
      max_codes: maxCodes,
      step,
      mode: runMode,
    }
    if (!useAutoWin && isStart && isEnd && oosStart && oosEnd) {
      body.is_start = isStart
      body.is_end = isEnd
      body.oos_start = oosStart
      body.oos_end = oosEnd
    }
    if (runMode === 'single') {
      body.vol_ratio_min = single.vol_ratio_min
      body.strong_reset = single.strong_reset
      body.exit_window = single.exit_window
      body.stop_pct = single.stop_pct
    } else {
      body.grid = { ...gridSel }
    }
    return body
  }

  const launch = async (body: LabOptimizeBody) => {
    setErr('')
    setGuidedError(null)
    setBusy(true)
    setTab('console')
    try {
      const r = await api.labOptimize(body)
      setTask({
        task_id: r.task_id,
        status: 'pending',
        strategy: String(body.strategy),
        windows: r.windows,
        progress: 0,
        message: '已排队',
      })
      startPoll(r.task_id)
    } catch (e) {
      setGuidedError(e)
      const message = String(e)
      const activeTaskId = e instanceof ApiError && typeof e.details.active_task_id === 'string'
        ? e.details.active_task_id
        : message.match(/"active_task_id":"([^"]+)"/)?.[1]
      if (activeTaskId) {
        setErr('检测到已有实验任务，已恢复其进度。')
        setBusy(true)
        startPoll(activeTaskId)
      } else {
        setErr(e instanceof Error ? e.message : message)
        setBusy(false)
      }
    }
  }

  const run = async () => launch(buildBody())

  const runTrusted = async () => {
    if (!catalog) return
    await launch({
      strategy,
      max_codes: 600,
      step: 5,
      mode: 'grid',
      grid: { ...catalog.grid_default },
    })
  }

  const cancelTask = async () => {
    if (!task?.task_id) return
    try {
      await api.labCancel(task.task_id)
      setBusy(false)
      stopPoll()
      setTask({ ...task, status: 'cancelled', message: '已取消' })
    } catch (e) {
      setErr(String(e))
    }
  }

  const isRunning = busy && !TERMINAL_TASK_STATES.includes(task?.status || '')
  const plan = research?.plan
  const stratDoc = catalog?.strategies?.[strategy]
  const reportForDisplay = (lastResult?.trusted_report || latestReport) ?? null

  useEffect(() => {
    const state = location.state as { openLabConclusion?: boolean } | null
    const requested = new URLSearchParams(location.search).get('view') === 'results'
      || state?.openLabConclusion === true
    if (!requested) return
    if (viewMode === 'advanced') setTab('results')
    if (!reportForDisplay) return
    const frame = window.requestAnimationFrame(() => {
      document.getElementById('lab-conclusion')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [location.key, location.search, location.state, reportForDisplay, viewMode])

  const chartOption: EChartsOption = useMemo(() => {
    const rows = (lastResult?.is_top || isBoard.rows || []).slice(0, 8)
    const labels = rows.map((r, i) => `#${i + 1}`)
    const pfs = rows.map((r) => Number(r.net_profit_factor ?? 0))
    const wrs = rows.map((r) => {
      const n = Number(r.net_win_rate ?? 0)
      return Math.abs(n) <= 1.5 ? n * 100 : n
    })
    return {
      backgroundColor: 'transparent',
      grid: { left: 40, right: 16, top: 28, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { data: ['净PF', '净胜率%'], textStyle: { color: c.subtext, fontSize: 11 }, top: 0 },
      xAxis: {
        type: 'category',
        data: labels.length ? labels : ['—'],
        axisLabel: { color: c.subtext, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axis } },
      },
      yAxis: [
        {
          type: 'value',
          axisLabel: { color: c.subtext, fontSize: 10 },
          splitLine: { lineStyle: { color: c.split, type: 'dashed' } },
        },
        {
          type: 'value',
          axisLabel: { color: c.subtext, fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '净PF',
          type: 'bar',
          data: pfs.length ? pfs : [0],
          barMaxWidth: 26,
          itemStyle: { color: c.accent, borderRadius: [4, 4, 0, 0] },
        },
        {
          name: '净胜率%',
          type: 'line',
          yAxisIndex: 1,
          data: wrs.length ? wrs : [0],
          smooth: true,
          symbolSize: 5,
          lineStyle: { width: 2, color: c.accent2 },
          itemStyle: { color: c.accent2 },
        },
      ],
    } as EChartsOption
  }, [lastResult, isBoard.rows, c])

  const fillSingleFromRow = (row: LabMetricRow) => {
    setSingle({
      vol_ratio_min: Number(row.vol_ratio_min ?? 1.5),
      strong_reset: Number(row.strong_reset ?? 3),
      exit_window: Number(row.exit_window ?? 10),
      stop_pct: Number(row.stop_pct ?? 0.07),
    })
    if (row.strategy === 'A' || row.strategy === 'B') setStrategy(row.strategy)
    setRunMode('single')
    setTab('console')
  }

  if (viewMode === 'guided') {
    return (
      <LabGuided
        strategy={strategy}
        onStrategy={setStrategy}
        catalog={catalog}
        research={research}
        task={task}
        report={reportForDisplay}
        error={guidedError}
        running={isRunning}
        onRun={() => { void runTrusted() }}
        onCancel={() => { void cancelTask() }}
        onAdvanced={() => setViewMode('advanced')}
      />
    )
  }

  return (
    <div className="lab-page">
      <ViewModeToggle mode="advanced" onChange={() => setViewMode('guided')} />
      {/* header */}
      <section className="lab-hero">
        <div className="lab-hero-main">
          <div className="lab-kicker">Research Console</div>
          <h1 className="lab-title">策略实验室</h1>
          <p className="lab-sub">
            看清方案 A/B 的入场出场规则 → 冻结净成本 IS 第一名 → 跑 OOS、三窗 WF 与双基线 → 生成可信报告。
            结果<strong>不是</strong>买卖清单；可交易候选在{' '}
            <Link to="/" style={{ color: 'var(--accent)', fontWeight: 600 }}>选股总览 A 池</Link>。
          </p>
          <div className="lab-pipeline" style={{ marginTop: 14 }}>
            {(catalog?.pipeline || [
              { id: '1', name: '方案', desc: '' },
              { id: '2', name: '参数', desc: '' },
              { id: '3', name: '回放', desc: '' },
              { id: '4', name: '解读', desc: '' },
            ]).map((p, i) => (
              <div key={p.id} style={{ display: 'contents' }}>
                {i > 0 && <span className="lab-pipe-arrow">→</span>}
                <div className="lab-pipe-step">
                  <span className="n">{i + 1}</span>
                  {p.name}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="lab-metrics">
          <div className={`lab-metric ${plan?.data_ready_for_edge_validation ? 'ok' : 'warn'}`}>
            <div className="lbl">研究模式</div>
            <div className="val" style={{ fontSize: 14 }}>{plan?.label || '…'}</div>
            <div className="hint">{plan?.data_ready_for_edge_validation ? '可运行可信门禁' : '仅摸底'}</div>
          </div>
          <div className="lab-metric">
            <div className="lbl">日线覆盖</div>
            <div className="val">{plan ? `${plan.n_dates} 日` : '—'}</div>
            <div className="hint mono">{plan ? `${plan.earliest}→${plan.latest}` : '—'}</div>
          </div>
          <div className="lab-metric">
            <div className="lbl">当前方案</div>
            <div className="val">方案 {strategy}</div>
            <div className="hint">{stratDoc?.name || '—'}</div>
          </div>
          <div className="lab-metric">
            <div className="lbl">本次组合数</div>
            <div className="val">{runMode === 'single' ? 1 : nCombos}</div>
            <div className="hint">预估 ~{estMin} 分钟 · {maxCodes} 只</div>
          </div>
        </div>
      </section>

      {research?.need_backfill && (
        <div className="lab-banner">
          <span className="ico">⚠</span>
          <div>
            历史深度不足（降级窗）。扩容：<code>python sync_history.py</code> →{' '}
            <code>python research_status.py</code> 至 mode=full。
          </div>
        </div>
      )}

      {/* tabs */}
      <div className="lab-tabs">
        {(
          [
            ['playbook', '① 方案说明书'],
            ['console', '② 参数台'],
            ['results', '③ 结果与明细'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`lab-tab ${tab === id ? 'active' : ''}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── PLAYBOOK ── */}
      {tab === 'playbook' && (
        <>
          <section className="card">
            <div className="lab-section-head">
              <h2>选中要研究的方案</h2>
              <span className="grow" />
              <span className="badge badge-mute">点卡片切换 · 再去参数台运行</span>
            </div>
            <div className="lab-strat-detail">
              {(['A', 'B'] as StratId[]).map((id) => {
                const s = catalog?.strategies?.[id]
                if (!s) {
                  return (
                    <article key={id}>
                      <div className="sid">SCHEME {id}</div>
                      <h3>加载中…</h3>
                    </article>
                  )
                }
                return (
                  <article
                    key={id}
                    className={strategy === id ? 'selected' : ''}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setStrategy(id)}
                  >
                    <div className="sid">SCHEME {id}{strategy === id ? ' · 已选' : ''}</div>
                    <h3>{s.name}</h3>
                    <div className="tagline">{s.tagline}</div>
                    <h4>{s.entry_title}</h4>
                    <ol>
                      {s.entry_steps.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ol>
                    <h4>{s.exit_title}</h4>
                    <ol>
                      {s.exit_steps.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ol>
                    <div className="note">{s.fixed_note}</div>
                    <div style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className={`btn ${strategy === id ? 'primary' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          setStrategy(id)
                          setTab('console')
                        }}
                      >
                        用方案 {id} 去调参
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>

          <section className="card">
            <div className="lab-section-head">
              <h2>可调参数词典</h2>
              <span className="badge badge-mute">网格 / 单组试跑共用</span>
            </div>
            <div className="lab-param-glossary">
              {(catalog?.params || []).map((p) => (
                <div key={p.key} className="lab-param-card">
                  <div className="pk">{p.key}</div>
                  <div className="pn">{p.name} · {p.unit}</div>
                  <div className="pm">{p.meaning}</div>
                  <div className="pd">
                    默认 <b>{p.default}</b>
                    {p.unit === '比例' ? ` (${(p.default * 100).toFixed(0)}%)` : ''}
                    {' · '}影响：{p.affects}
                    <br />
                    可选 {p.options.join(' / ')} · 建议 {p.range_hint}
                  </div>
                </div>
              ))}
              {!catalog && <div className="muted">加载参数说明…</div>}
            </div>
          </section>
        </>
      )}

      {/* ── CONSOLE ── */}
      {tab === 'console' && (
        <section className="card">
          <div className="lab-section-head">
            <h2>参数台 · 方案 {strategy}</h2>
            <span className="grow" />
            <button type="button" className="btn" onClick={() => setTab('playbook')}>
              查看方案说明书
            </button>
          </div>

          <div className="lab-mode-row" style={{ marginBottom: 16 }}>
            <button
              type="button"
              className={`lab-mode-pill ${runMode === 'grid' ? 'on' : ''}`}
              onClick={() => setRunMode('grid')}
              disabled={isRunning}
            >
              <strong>网格搜索</strong>
              勾选多档参数，自动展开组合排序
            </button>
            <button
              type="button"
              className={`lab-mode-pill ${runMode === 'single' ? 'on' : ''}`}
              onClick={() => setRunMode('single')}
              disabled={isRunning}
            >
              <strong>单组试跑</strong>
              人工指定一组参数，看 IS + OOS
            </button>
            <div className="lab-combo-count">
              {runMode === 'single'
                ? `单组：量比 ${single.vol_ratio_min} · 清零 ${single.strong_reset} · 窗 ${single.exit_window} · 止损 ${stopShow(single.stop_pct)}`
                : `网格将跑 ${nCombos} 组（默认全集 ${catalog?.grid_combo_count ?? '—'}）`}
            </div>
          </div>

          {runMode === 'grid' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 16 }}>
              {(catalog?.params || []).map((p) => (
                <div key={p.key}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    {p.name}
                    <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                      {p.meaning.slice(0, 42)}…
                    </span>
                  </div>
                  <div className="lab-chips">
                    {p.options.map((v) => (
                      <button
                        key={v}
                        type="button"
                        className={`lab-chip ${(gridSel[p.key] || []).includes(v) ? 'on' : ''}`}
                        disabled={isRunning}
                        onClick={() => toggleChip(p.key, v)}
                      >
                        {p.key === 'stop_pct' ? `${(v * 100).toFixed(0)}%` : v}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="lab-form-grid" style={{ marginBottom: 16 }}>
              {(catalog?.params || []).map((p) => (
                <div key={p.key} className="lab-field">
                  <label>{p.name}</label>
                  {p.options?.length ? (
                    <select
                      disabled={isRunning}
                      value={String(single[p.key as keyof typeof single])}
                      onChange={(e) =>
                        setSingle((s) => ({
                          ...s,
                          [p.key]: p.key === 'strong_reset' || p.key === 'exit_window'
                            ? Number(e.target.value)
                            : Number(e.target.value),
                        }))
                      }
                    >
                      {p.options.map((v) => (
                        <option key={v} value={v}>
                          {p.key === 'stop_pct' ? `${(v * 100).toFixed(0)}%` : v}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="number"
                      step="any"
                      disabled={isRunning}
                      value={single[p.key as keyof typeof single]}
                      onChange={(e) =>
                        setSingle((s) => ({ ...s, [p.key]: Number(e.target.value) }))
                      }
                    />
                  )}
                  <span className="muted" style={{ fontSize: 11 }}>{p.range_hint}</span>
                </div>
              ))}
            </div>
          )}

          <div className="lab-form-grid" style={{ marginBottom: 12 }}>
            <div className="lab-field">
              <label>方案</label>
              <select
                value={strategy}
                disabled={isRunning}
                onChange={(e) => setStrategy(e.target.value as StratId)}
              >
                <option value="A">A · 形态突破</option>
                <option value="B">B · 五步抓主升</option>
              </select>
            </div>
            <div className="lab-field">
              <label>采样股票数</label>
              <select
                value={maxCodes}
                disabled={isRunning}
                onChange={(e) => setMaxCodes(Number(e.target.value))}
              >
                <option value={100}>100 · 冒烟</option>
                <option value={200}>200 · 日常</option>
                <option value={400}>400</option>
                <option value={600}>600 · 推荐</option>
                <option value={1000}>1000</option>
                <option value={4500}>全市场</option>
              </select>
            </div>
            <div className="lab-field">
              <label>采样步长</label>
              <select value={step} disabled={isRunning} onChange={(e) => setStep(Number(e.target.value))}>
                <option value={5}>5 · 密</option>
                <option value={10}>10 · 标准</option>
                <option value={15}>15 · 快</option>
                <option value={20}>20 · 极快</option>
              </select>
            </div>
            <div className="lab-field">
              <label>时间窗</label>
              <select
                value={useAutoWin ? 'auto' : 'manual'}
                disabled={isRunning}
                onChange={(e) => setUseAutoWin(e.target.value === 'auto')}
              >
                <option value="auto">自动（研究就绪窗）</option>
                <option value="manual">手动指定</option>
              </select>
            </div>
          </div>

          {!useAutoWin && (
            <div className="lab-form-grid" style={{ marginBottom: 12 }}>
              <div className="lab-field">
                <label>IS 起 YYYYMMDD</label>
                <input value={isStart} disabled={isRunning} onChange={(e) => setIsStart(e.target.value)} />
              </div>
              <div className="lab-field">
                <label>IS 止</label>
                <input value={isEnd} disabled={isRunning} onChange={(e) => setIsEnd(e.target.value)} />
              </div>
              <div className="lab-field">
                <label>OOS 起</label>
                <input value={oosStart} disabled={isRunning} onChange={(e) => setOosStart(e.target.value)} />
              </div>
              <div className="lab-field">
                <label>OOS 止</label>
                <input value={oosEnd} disabled={isRunning} onChange={(e) => setOosEnd(e.target.value)} />
              </div>
              <div className="lab-field" style={{ justifyContent: 'flex-end' }}>
                <label>&nbsp;</label>
                <button type="button" className="btn" onClick={applyRecommendedWindow}>
                  填入推荐窗
                </button>
              </div>
            </div>
          )}

          {useAutoWin && plan && (
            <div className="lab-est" style={{ marginBottom: 12 }}>
              自动窗 · IS <b className="mono">{plan.is_start}~{plan.is_end}</b>
              {' · '}OOS <b className="mono">{plan.oos_start}~{plan.oos_end}</b>
              {' · '}{plan.label}
            </div>
          )}

          <div className="lab-actions">
            {!isRunning ? (
              <button
                type="button"
                className="btn primary"
                disabled={plan?.mode === 'insufficient'}
                onClick={run}
              >
                {runMode === 'single' ? `试跑方案 ${strategy} 单组` : `优化方案 ${strategy} · ${nCombos} 组`}
              </button>
            ) : (
              <button type="button" className="btn danger" onClick={cancelTask}>
                取消任务
              </button>
            )}
            <button type="button" className="btn" onClick={loadBoards} disabled={isRunning}>
              刷新结果
            </button>
            <span className="muted" style={{ fontSize: 12 }}>
              预估 ~{estMin} 分钟
            </span>
          </div>

          {task && (
            <div className={`lab-progress ${task.status === 'done' ? 'done' : task.status === 'error' ? 'error' : ''}`} style={{ marginTop: 14 }}>
              <div className="lab-progress-head">
                <span>
                  任务 <span className="mono">{task.task_id}</span>
                  {task.strategy ? ` · ${task.strategy}` : ''}
                </span>
                <span>
                  {task.status === 'done' && '完成'}
                  {task.status === 'error' && '失败'}
                  {task.status === 'cancelled' && '已取消'}
                  {task.status === 'interrupted' && '服务重启中断 · 可按原配置续跑'}
                  {(task.status === 'running' || task.status === 'pending') &&
                    `${task.status === 'pending' ? '排队' : '运行'} ${task.progress ?? 0}%`}
                </span>
              </div>
              <div className="lab-progress-bar">
                <i style={{ width: `${Math.max(Number(task.progress || 0), task.status === 'pending' ? 4 : 0)}%` }} />
              </div>
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--muted)' }}>
                {task.phase ? `[${task.phase}] ` : ''}{task.status === 'error' ? task.error : task.message || '—'}
              </div>
            </div>
          )}
          {err && <div className="err-inline" style={{ marginTop: 8 }}>{err}</div>}
        </section>
      )}

      {/* ── RESULTS ── */}
      {tab === 'results' && (
        <>
          {(lastResult?.trusted_report || latestReport) && (
            <div id="lab-conclusion">
              <LabTrustedReportView
                report={(lastResult?.trusted_report || latestReport) as LabTrustedReport}
                history={reportHistory}
                onSelectHistory={(runId) => {
                  api.labReport(runId)
                    .then((envelope) => setLatestReport(envelope.report))
                    .catch((e) => setErr(String(e)))
                }}
              />
            </div>
          )}
          <section className="card" style={{ marginBottom: 14 }}>
            <strong>研究回测 · 不会生成订单</strong>
            <p className="note" style={{ margin: '6px 0 0' }}>
              本页“净PF / 净胜率 / 净回撤”已计佣金、最低收费、印花税、其他费用与滑点；
              只有 full 数据窗且净成本 OOS、Walk-forward 和基线门禁全部通过，才可晋级候选档案。
              A 池与纸面交易仍需独立扫描、行情和人工确认。
            </p>
          </section>
          <div className="lab-boards">
            <div className="card">
              <div className="lab-section-head">
                <h2>样本内 IS</h2>
                <span className="grow" />
                <span className="badge badge-mute">{lastResult ? '最近任务' : isBoard.source || '库'}</span>
              </div>
              <BoardTable
                rows={lastResult?.is_all || lastResult?.is_top || isBoard.rows || []}
                emptyHint="到「参数台」跑网格或单组试跑。"
                selectedIdx={selectedKind === 'IS' ? selectedIdx : null}
                onSelect={(i, row) => {
                  setSelectedIdx(i)
                  setSelected(row)
                  setSelectedKind('IS')
                }}
              />
            </div>
            <div className="card">
              <div className="lab-section-head">
                <h2>样本外 OOS</h2>
                <span className="grow" />
                <span className="badge badge-mute">{lastResult ? '最近任务' : oosBoard.source || '库'}</span>
              </div>
              <BoardTable
                rows={lastResult?.oos || oosBoard.rows || []}
                emptyHint="网格会把 IS Top 拿到 OOS 验证；单组会直接出 OOS 一行。"
                selectedIdx={selectedKind === 'OOS' ? selectedIdx : null}
                onSelect={(i, row) => {
                  setSelectedIdx(i)
                  setSelected(row)
                  setSelectedKind('OOS')
                }}
                oosMode
              />
            </div>
          </div>

          <div className="lab-bottom">
            <div className="card">
              <div className="lab-section-head">
                <h2>IS Top 图</h2>
              </div>
              {(lastResult?.is_top || isBoard.rows || []).length ? (
                <EChart option={chartOption} height={240} />
              ) : (
                <div className="lab-empty"><strong>无图</strong>先跑一轮优化</div>
              )}
            </div>

            <div className="card">
              <div className="lab-section-head">
                <h2>选中参数明细</h2>
                <span className="grow" />
                {selected && (
                  <button type="button" className="btn" onClick={() => fillSingleFromRow(selected)}>
                    载入单组试跑
                  </button>
                )}
              </div>
              {selected ? (
                <div className="lab-inspector">
                  <h3>
                    方案 {String(selected.strategy ?? '—')} · {selectedKind}
                  </h3>
                  <div className="kv"><span className="k">建仓量比</span><span className="v">{fmt(selected.vol_ratio_min, 2)}</span></div>
                  <div className="kv"><span className="k">强势清零</span><span className="v">{fmt(selected.strong_reset, 0)} 根</span></div>
                  <div className="kv"><span className="k">出货窗口</span><span className="v">{fmt(selected.exit_window, 0)} 日</span></div>
                  <div className="kv"><span className="k">止损</span><span className="v">{stopShow(selected.stop_pct)}</span></div>
                  <div className="kv">
                    <span className="k">交易数</span>
                    <span className="v">
                      {fmt(selectedKind === 'OOS' ? selected.oos_net_n_trades : selected.net_n_trades, 0)}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">净胜率</span>
                    <span className="v">
                      {pct(selectedKind === 'OOS' ? selected.oos_net_win_rate : selected.net_win_rate)}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">净 Profit Factor</span>
                    <span className={`v ${pfClass(selectedKind === 'OOS' ? selected.oos_net_profit_factor : selected.net_profit_factor)}`}>
                      {fmt(selectedKind === 'OOS' ? selected.oos_net_profit_factor : selected.net_profit_factor, 3)}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">净最大回撤</span>
                    <span className="v">
                      {pct(selectedKind === 'OOS' ? selected.oos_net_max_drawdown : selected.net_max_drawdown)}
                    </span>
                  </div>
                  {selected.is_net_profit_factor != null && (
                    <div className="kv"><span className="k">IS 净PF（对照）</span><span className="v">{fmt(selected.is_net_profit_factor, 3)}</span></div>
                  )}
                  <p className="note" style={{ marginTop: 12 }}>
                    {catalog?.params?.map((p) => (
                      <span key={p.key}>
                        <b>{p.name}</b>={String(selected[p.key as keyof LabMetricRow] ?? '—')}；
                      </span>
                    ))}
                    点「载入单组试跑」可改一档再跑。
                  </p>
                </div>
              ) : (
                <div className="lab-empty">
                  <strong>点表格一行</strong>
                  查看该组参数含义与 IS/OOS 指标
                </div>
              )}
            </div>
          </div>

          <section className="card">
            <div className="lab-section-head">
              <h2>擂台注册表</h2>
              <span className="badge badge-ok">
                active {arena?.rows.filter((r) => r.status === 'active').length ?? 0}
              </span>
            </div>
            {arena && arena.rows.length > 0 ? (
              <div className="lab-table-wrap">
                <table className="lab-table">
                  <thead>
                    <tr>
                      <th>状态</th>
                      <th>方案</th>
                      <th className="num">IS 净PF</th>
                      <th className="num">OOS 净PF</th>
                      <th>WF</th>
                    </tr>
                  </thead>
                  <tbody>
                    {arena.rows.slice(0, 12).map((r, i) => (
                      <tr
                        key={i}
                        style={{ cursor: 'pointer' }}
                        onClick={() => {
                          setSelected(r)
                          setSelectedIdx(i)
                          setSelectedKind('OOS')
                        }}
                      >
                        <td>
                          <span className={r.status === 'active' ? 'badge badge-ok' : 'badge'}>
                            {String(r.status)}
                          </span>
                        </td>
                        <td>{String(r.strategy ?? '—')}</td>
                        <td className="num">{fmt(r.is_net_profit_factor, 3)}</td>
                        <td className={`num ${pfClass(r.oos_net_profit_factor)}`}>{fmt(r.oos_net_profit_factor, 3)}</td>
                        <td>{r.wf_pass === 1 || r.wf_pass === true ? '通过' : r.wf_pass === 0 ? '未过' : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="lab-empty">
                <strong>尚未播种</strong>
                只有可信门禁 PASS 才会登记为隔离候选；不会自动写入 active
              </div>
            )}
          </section>
        </>
      )}

      <p className="lab-footnote">
        {catalog?.disclaimer || research?.disclaimer || '研究辅助，不是投资建议。'}
      </p>
    </div>
  )
}
