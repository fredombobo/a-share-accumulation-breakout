import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, BacktestRunBody, BacktestTaskResult, BacktestTaskStatus, BacktestTrade } from '../api/client'
import { useChartColors } from '../theme/ThemeContext'
import EChart from '../components/EChart'
import TradeChart from '../components/TradeChart'
import { IcoCheck, IcoRefresh, IcoScan, IcoStop, IcoTarget } from '../components/Icons'
import type { EChartsOption } from 'echarts'

type FormState = {
  strategy: 'A' | 'B'
  vol_ratio_min: number
  stop_pct: number
  exit_window: number
  strong_reset: number
  box_min_days: number | null
  box_max_days: number | null
  box_max_amp: number | null
  breakout_vol_ratio: number | null
  breakout_chg_min: number | null
  breakout_chg_max: number | null
  breakout_window_days: number | null
  breakout_vs_recent_vol_ratio: number | null
  require_ma60: boolean
  max_pullbacks: number | null
  commission_bps: number
  slippage_bps: number
  max_codes: number
  step: number
  winMode: 'auto' | 'manual'
  is_start: string
  is_end: string
  oos_start: string
  oos_end: string
  include_wf: boolean
  include_baselines: boolean
}

const DEFAULTS: FormState = {
  strategy: 'A',
  vol_ratio_min: 1.5,
  stop_pct: 0.07,
  exit_window: 10,
  strong_reset: 3,
  box_min_days: null,
  box_max_days: null,
  box_max_amp: null,
  breakout_vol_ratio: null,
  breakout_chg_min: null,
  breakout_chg_max: null,
  breakout_window_days: null,
  breakout_vs_recent_vol_ratio: null,
  require_ma60: true,
  max_pullbacks: null,
  commission_bps: 5,
  slippage_bps: 10,
  max_codes: 600,
  step: 10,
  winMode: 'auto',
  is_start: '20230801',
  is_end: '20250731',
  oos_start: '20250801',
  oos_end: '20260731',
  include_wf: true,
  include_baselines: true,
}

const PRESETS: { id: string; name: string; patch: Partial<FormState>; desc: string }[] = [
  { id: 'default', name: '系统默认', patch: { ...DEFAULTS }, desc: '标杆量出场 · 万五成本' },
  { id: 'conservative', name: '保守防守', patch: { stop_pct: 0.05, exit_window: 7, strong_reset: 2, vol_ratio_min: 1.8 }, desc: '更早止损 · 更严放量' },
  { id: 'aggressive', name: '激进进攻', patch: { stop_pct: 0.09, exit_window: 15, strong_reset: 4, vol_ratio_min: 1.3 }, desc: '更宽止损 · 更长持有' },
  { id: 'flow', name: '资金流加持', patch: { vol_ratio_min: 1.6, exit_window: 12, strong_reset: 3, breakout_vs_recent_vol_ratio: 1.4 }, desc: '双重放量确认' },
]

interface HistoryEntry {
  ts: string
  hash: string
  label: string
  oosPf: number | null
  oosWr: number | null
  oosN: number | null
  oosDd: number | null
  form: FormState
}

const HISTORY_KEY = 'ab-bt-history'

function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '—'
  return v.toFixed(digits)
}
function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(2)}%`
}

function formHash(f: FormState): string {
  const s = JSON.stringify(f)
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h).toString(36)
}

function loadHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') as HistoryEntry[]
  } catch {
    return []
  }
}

function saveHistory(entry: HistoryEntry) {
  const list = [entry, ...loadHistory()].slice(0, 50)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list))
}

function Num({ label, value, onChange, step = 0.01, min, max, hint, wide }: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
  step?: number
  min?: number
  max?: number
  hint?: string
  wide?: boolean
}) {
  return (
    <div className={`lab-field ${wide ? 'wide' : ''}`} style={wide ? { gridColumn: '1 / -1' } : undefined}>
      <label>{label}{hint && <span style={{ fontWeight: 400, opacity: 0.75, marginLeft: 6 }}>{hint}</span>}</label>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />
    </div>
  )
}

export default function BacktestStudio() {
  const c = useChartColors()
  const [form, setForm] = useState<FormState>(DEFAULTS)
  const [task, setTask] = useState<BacktestTaskStatus | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory)
  const [exported, setExported] = useState('')
  const [selectedTrade, setSelectedTrade] = useState<BacktestTrade | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const running = task?.status === 'running'

  const stopPolling = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
  }, [])

  const poll = useCallback((id: string) => {
    api.backtestStatus(id)
      .then((st) => {
        setTask(st)
        if (st.status === 'running') {
          timerRef.current = setTimeout(() => poll(id), 1500)
        } else if (st.status === 'done' && st.result) {
          const m = st.result.oos?.metrics || {}
          const label = `${st.result.params.strategy}·vr${(st.result.params.exit as Record<string, number>)?.vol_ratio_min ?? '—'}·st${(st.result.params.exit as Record<string, number>)?.stop_pct ?? '—'}`
          saveHistory({
            ts: new Date().toISOString(), hash: formHash(form), label,
            oosPf: m.net_profit_factor ?? null, oosWr: m.net_win_rate ?? null,
            oosN: m.net_n_trades ?? null, oosDd: m.net_max_drawdown ?? null, form,
          })
          setHistory(loadHistory())
        } else if (st.status === 'error') {
          setErr(st.error || '回测失败')
        }
      })
      .catch((e: unknown) => {
        setErr(String(e))
        stopPolling()
      })
  }, [form, stopPolling])

  useEffect(() => stopPolling, [stopPolling])

  const onRun = async (e: FormEvent) => {
    e.preventDefault()
    setErr('')
    setExported('')
    const body: BacktestRunBody = {
      strategy: form.strategy,
      vol_ratio_min: form.vol_ratio_min,
      stop_pct: form.stop_pct,
      exit_window: form.exit_window,
      strong_reset: form.strong_reset,
      signal: {
        box_min_days: form.box_min_days,
        box_max_days: form.box_max_days,
        box_max_amp: form.box_max_amp,
        breakout_vol_ratio: form.breakout_vol_ratio,
        breakout_chg_min: form.breakout_chg_min,
        breakout_chg_max: form.breakout_chg_max,
        breakout_window_days: form.breakout_window_days,
        breakout_vs_recent_vol_ratio: form.breakout_vs_recent_vol_ratio,
        require_ma60: form.require_ma60,
        max_pullbacks: form.max_pullbacks,
      },
      costs: {
        commission_rate: form.commission_bps / 10000,
        slippage: form.slippage_bps / 10000,
      },
      windows: form.winMode === 'auto'
        ? { mode: 'auto' }
        : { mode: 'manual', is_start: form.is_start, is_end: form.is_end, oos_start: form.oos_start, oos_end: form.oos_end },
      max_codes: form.max_codes,
      step: form.step,
      include_wf: form.include_wf,
      include_baselines: form.include_baselines,
    }
    try {
      const resp = await api.backtestRun(body)
      setTaskId(resp.task_id)
      setTask({ status: 'running', stage: '排队中…', progress: 0, started_at: '', cancel_requested: false, result: null, error: null })
      poll(resp.task_id)
    } catch (e2) {
      setErr(String(e2))
    }
  }

  const onCancel = async () => {
    if (!taskId) return
    try { await api.backtestCancel(taskId) } catch (e2) { setErr(String(e2)) }
  }

  const equityOption = useMemo<EChartsOption | null>(() => {
    const pts = task?.result?.oos?.equity || []
    if (!pts.length) return null
    return {
      backgroundColor: 'transparent',
      grid: { left: 8, right: 50, top: 14, bottom: 4, containLabel: true },
      tooltip: { trigger: 'axis' as const, confine: true },
      xAxis: {
        type: 'category' as const, data: pts.map((p) => p.date), show: true,
        axisLine: { lineStyle: { color: c.axis } }, axisLabel: { color: c.subtext, fontSize: 10, formatter: (v: string) => v.slice(5).replace('-', '/') },
      },
      yAxis: [
        { type: 'value' as const, scale: true, position: 'left' as const, splitLine: { lineStyle: { color: c.split } }, axisLabel: { color: c.subtext, fontSize: 10, formatter: (v: number) => v.toFixed(2) } },
        { type: 'value' as const, scale: true, position: 'right' as const, axisLabel: { color: c.subtext, fontSize: 10, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { show: false } },
      ],
      series: [
        {
          name: '净值', type: 'line' as const, data: pts.map((p) => p.eq), smooth: true, showSymbol: false,
          lineStyle: { color: c.accent, width: 1.6 },
          areaStyle: {
            color: {
              type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: `${c.accent}33` },
                { offset: 1, color: `${c.accent}00` },
              ],
            },
          },
        },
        {
          name: '回撤', type: 'line' as const, yAxisIndex: 1, data: pts.map((p) => p.drawdown), smooth: true, showSymbol: false,
          lineStyle: { color: c.warn, width: 1, type: 'dashed' as const },
          areaStyle: { color: `${c.warn}22` },
        },
      ],
    }
  }, [task?.result?.oos?.equity, c])

  const result = task?.result ?? null
  const isM = result?.is?.metrics
  const oosM = result?.oos?.metrics
  const holdPf = result?.hold_ratio?.pf

  const exportJson = () => {
    if (!result) return
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest_${result.task_id}.json`
    a.click()
    URL.revokeObjectURL(url)
    setExported('已导出 JSON 报告')
  }

  const wf = (result?.wf || {}) as { wf_pass?: boolean; train_mean_pf?: number | null; oos_mean_pf?: number | null; wf_detail?: { window: string; train_pf: number | null; test_pf: number | null; test_dd: number | null; test_wr: number | null; test_n: number | null }[] }

  const exitDist = (m: BacktestTaskResult['oos']['metrics'] | undefined): [string, number][] =>
    Object.entries(m?.exits || {}).sort((a, b) => b[1] - a[1])

  const EXIT_LABEL: Record<string, string> = { stop: '止损', bench: '标杆量出货', target: '止盈', time: '到期强平' }

  return (
    <div className="bt-studio fade-up">
      {/* 参数面板 */}
      <form className="bt-side" onSubmit={onRun}>
        <div className="bt-side-head">
          <h2 style={{ margin: 0 }}>回测参数</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setForm(DEFAULTS); setErr('') }}>
            <IcoRefresh size={13} />重置
          </button>
        </div>

        <div className="lab-field">
          <label>策略</label>
          <div className="seg" style={{ width: '100%' }}>
            {(['A', 'B'] as const).map((s) => (
              <button key={s} type="button" className={`seg-item ${form.strategy === s ? 'on' : ''}`} style={{ flex: 1 }}
                onClick={() => set('strategy', s)}>
                {s === 'A' ? 'A · 横盘吸筹突破' : 'B · 五步抓主升'}
              </button>
            ))}
          </div>
        </div>

        <div className="lab-field">
          <label>预设模板</label>
          <div className="lab-chips">
            {PRESETS.map((p) => (
              <button key={p.id} type="button" className="lab-chip" title={p.desc}
                onClick={() => setForm((f) => ({ ...f, ...p.patch }))}>
                {p.name}
              </button>
            ))}
          </div>
        </div>

        <div className="bt-group">
          <div className="bt-group-title">出场参数（标杆量出货）</div>
          <div className="lab-form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <Num label="建仓放量倍数" hint="量/5日均量" value={form.vol_ratio_min} step={0.1} min={1} max={3} onChange={(v) => set('vol_ratio_min', v ?? 1.5)} />
            <Num label="兜底止损" hint="%" value={form.stop_pct === null ? null : Math.round(form.stop_pct * 100)} step={1} min={2} max={15} onChange={(v) => set('stop_pct', (v ?? 7) / 100)} />
            <Num label="出货计数窗口" hint="交易日" value={form.exit_window} step={1} min={3} max={30} onChange={(v) => set('exit_window', v ?? 10)} />
            <Num label="强势日重置" hint="连续强势清零" value={form.strong_reset} step={1} min={1} max={8} onChange={(v) => set('strong_reset', v ?? 3)} />
          </div>
        </div>

        <details className="bt-group">
          <summary className="bt-group-title" style={{ cursor: 'pointer' }}>信号阈值（箱体/突破，留空=系统默认）</summary>
          <div className="lab-form-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 10 }}>
            <Num label="横盘最短" hint="日" value={form.box_min_days} step={1} min={10} max={60} onChange={(v) => set('box_min_days', v)} />
            <Num label="横盘最长" hint="日" value={form.box_max_days} step={1} min={40} max={160} onChange={(v) => set('box_max_days', v)} />
            <Num label="箱体振幅上限" hint="0.26" value={form.box_max_amp} step={0.02} min={0.1} max={0.5} onChange={(v) => set('box_max_amp', v)} />
            <Num label="突破放量倍数" hint="vs 横盘均量" value={form.breakout_vol_ratio} step={0.1} min={1} max={4} onChange={(v) => set('breakout_vol_ratio', v)} />
            <Num label="突破最小涨幅" hint="0.02" value={form.breakout_chg_min} step={0.005} min={0} max={0.05} onChange={(v) => set('breakout_chg_min', v)} />
            <Num label="突破最大涨幅" hint="0.095" value={form.breakout_chg_max} step={0.005} min={0.05} max={0.15} onChange={(v) => set('breakout_chg_max', v)} />
            <Num label="突破确认窗口" hint="日" value={form.breakout_window_days} step={1} min={1} max={15} onChange={(v) => set('breakout_window_days', v)} />
            <Num label="双确认量比" hint="vs 前5日均量" value={form.breakout_vs_recent_vol_ratio} step={0.1} min={0.8} max={3} onChange={(v) => set('breakout_vs_recent_vol_ratio', v)} />
            <Num label="回踩容忍" hint="次（null=自动）" value={form.max_pullbacks} step={1} min={0} max={3} onChange={(v) => set('max_pullbacks', v)} />
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <label className="pill" style={{ cursor: 'pointer', gap: 6 }}>
              <input type="checkbox" checked={form.require_ma60} onChange={(e) => set('require_ma60', e.target.checked)} />
              要求站上 MA60（过滤底部震荡假突破）
            </label>
          </div>
        </details>

        <details className="bt-group">
          <summary className="bt-group-title" style={{ cursor: 'pointer' }}>交易成本（与可信门禁同口径）</summary>
          <div className="lab-form-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 10 }}>
            <Num label="佣金" hint="万X/边" value={form.commission_bps} step={0.5} min={0} max={20} onChange={(v) => set('commission_bps', v ?? 5)} />
            <Num label="滑点" hint="万X/边" value={form.slippage_bps} step={1} min={0} max={50} onChange={(v) => set('slippage_bps', v ?? 10)} />
          </div>
          <div className="lab-est" style={{ marginTop: 8 }}>
            印花税卖出千一、其他费万一已内置；最低佣金 5 元/边。默认万五与纸面交易一致。
          </div>
        </details>

        <div className="bt-group">
          <div className="bt-group-title">窗口与采样</div>
          <div className="lab-mode-row">
            {(['auto', 'manual'] as const).map((m) => (
              <button key={m} type="button" className={`lab-mode-pill ${form.winMode === m ? 'on' : ''}`} style={{ minWidth: 110 }}
                onClick={() => set('winMode', m)}>
                <strong>{m === 'auto' ? '自动窗口' : '手动窗口'}</strong>
                {m === 'auto' ? '按数据深度推荐' : '自定 IS/OOS'}
              </button>
            ))}
          </div>
          {form.winMode === 'manual' && (
            <div className="lab-form-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 10 }}>
              <Num label="IS 开始" hint="YYYYMMDD" value={null} onChange={() => undefined} />
              <input type="text" className="input" style={{ gridColumn: '1 / -1' }} value={`${form.is_start} ~ ${form.is_end}`} readOnly />
              <input type="text" className="input" style={{ gridColumn: '1 / -1' }} value={`${form.oos_start} ~ ${form.oos_end}`} readOnly />
              <div className="lab-est" style={{ gridColumn: '1 / -1' }}>手动窗口请修改右侧默认值（开发中直接编辑日期）</div>
            </div>
          )}
          <div className="lab-form-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 10 }}>
            <Num label="宇宙股票数" hint="越大越慢" value={form.max_codes} step={100} min={100} max={4500} onChange={(v) => set('max_codes', v ?? 600)} />
            <Num label="采样步长" hint="交易日" value={form.step} step={1} min={1} max={60} onChange={(v) => set('step', v ?? 10)} />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <label className="pill" style={{ cursor: 'pointer', gap: 6 }}>
              <input type="checkbox" checked={form.include_wf} onChange={(e) => set('include_wf', e.target.checked)} />
              三窗 Walk-forward
            </label>
            <label className="pill" style={{ cursor: 'pointer', gap: 6 }}>
              <input type="checkbox" checked={form.include_baselines} onChange={(e) => set('include_baselines', e.target.checked)} />
              随机/MA 双基线
            </label>
          </div>
        </div>

        <div className="bt-actions">
          <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '11px 0', fontSize: 14 }} disabled={running}>
            <IcoScan size={15} />{running ? '回测进行中…' : '开始回测（净成本 IS/OOS）'}
          </button>
          {running && (
            <button type="button" className="btn btn-danger" style={{ width: '100%' }} onClick={onCancel}>
              <IcoStop size={14} />取消回测
            </button>
          )}
        </div>
        {err && <div className="err">{err}</div>}
      </form>

      {/* 结果区 */}
      <div className="bt-main">
        {!task && (
          <div className="empty">
            <strong>回测工作台</strong>
            左侧配置参数后点击「开始回测」。系统将按 ENTRY v1（突破日次日开盘）在样本内/样本外窗口回放，
            输出净成本指标、权益曲线与逐笔明细。{form.max_codes} 只 × step {form.step} 约需 3~8 分钟（含 WF/基线约 8~12 分钟）。
          </div>
        )}

        {task && task.status === 'running' && (
          <div className="card">
            <div className="h-sec" style={{ marginBottom: 12 }}>
              <h2 style={{ margin: 0 }}>回测进行中</h2>
              <span className="pill accent" style={{ gap: 6 }}><IcoTarget size={12} />净成本口径</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
              <span>{task.stage}</span>
              <span className="num">{task.progress}%</span>
            </div>
            <div className="progress"><i style={{ width: `${task.progress}%` }} /></div>
            <div className="note">全市场回放较耗时：IS/OOS 各 1~4 分钟，WF 约 3~5 分钟。完成后自动展示结果并登记试验历史。</div>
          </div>
        )}

        {task && task.status === 'cancelled' && <div className="empty"><strong>已取消</strong>结果未保留，可重新运行。</div>}
        {task && task.status === 'error' && <div className="empty"><strong>回测失败</strong>{task.error || err || '未知错误'}</div>}

        {task && task.status === 'done' && result && (
          <div className="bt-results">
            <div className="h-sec">
              <div>
                <div className="kicker" style={{ color: 'var(--accent)', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em' }}>
                  BACKTEST REPORT · {result.windows.mode === 'auto' ? '自动窗' : '手动窗'}
                </div>
                <h2 style={{ margin: '2px 0 0', fontSize: 17 }}>
                  {result.params.strategy === 'A' ? 'A · 横盘吸筹突破' : 'B · 五步抓主升'}
                  <span className="tag">IS {result.windows.is[0]}~{result.windows.is[1]} · OOS {result.windows.oos[1].slice(0, 4)}-{result.windows.oos[1].slice(4)} 后</span>
                </h2>
              </div>
              <div className="row">
                {holdPf !== null && holdPf !== undefined && (
                  <span className={`pill ${holdPf >= 0.8 ? 'ok' : 'danger'}`}>
                    OOS/IS PF 保持 {holdPf.toFixed(2)}{holdPf >= 0.8 ? ' ✓' : ' ✗'}
                  </span>
                )}
                <button className="btn btn-sm" onClick={exportJson}>导出 JSON</button>
              </div>
              {exported && <span className="badge badge-ok" style={{ marginLeft: 8 }}>{exported}</span>}
            </div>

            {/* KPI 带（OOS 净指标为主） */}
            <div className="metrics" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))' }}>
              <div className="kpi"><div className="kpi-label">OOS 净盈亏比</div>
                <div className="kpi-value" style={{ color: (oosM?.net_profit_factor ?? 0) >= 1 ? 'var(--ok-ink)' : 'var(--up-ink)' }}>{fmt(oosM?.net_profit_factor, 2)}</div></div>
              <div className="kpi"><div className="kpi-label">OOS 净胜率</div><div className="kpi-value">{pct(oosM?.net_win_rate)}</div></div>
              <div className="kpi"><div className="kpi-label">OOS 净均收益</div>
                <div className="kpi-value" style={{ color: (oosM?.net_avg_return ?? 0) >= 0 ? 'var(--ok-ink)' : 'var(--up-ink)' }}>{pct(oosM?.net_avg_return)}</div></div>
              <div className="kpi"><div className="kpi-label">OOS 净最大回撤</div>
                <div className="kpi-value" style={{ color: (oosM?.net_max_drawdown ?? 1) <= 0.25 ? 'var(--ok-ink)' : 'var(--up-ink)' }}>{pct(oosM?.net_max_drawdown)}</div></div>
              <div className="kpi"><div className="kpi-label">OOS 净交易数</div><div className="kpi-value">{fmt(oosM?.net_n_trades, 0)}</div></div>
              <div className="kpi"><div className="kpi-label">净费用合计</div>
                <div className="kpi-value" style={{ fontSize: 16 }}>¥{fmt((oosM?.commission ?? 0) + (oosM?.stamp_tax ?? 0) + (oosM?.other_fee ?? 0) + (oosM?.slippage_cost ?? 0), 0)}</div></div>
            </div>

            {/* IS vs OOS 对比 */}
            <div className="card" style={{ marginBottom: 14, padding: '14px 18px' }}>
              <div className="h-sec" style={{ marginBottom: 8 }}><h2 style={{ margin: 0, fontSize: 14 }}>样本内 vs 样本外</h2></div>
              <div className="bt-compare">
                {(['is', 'oos'] as const).map((side) => {
                  const m = side === 'is' ? isM : oosM
                  return (
                    <div key={side} className="bt-compare-col">
                      <div className="bt-compare-head">{side === 'is' ? '样本内 IS' : '样本外 OOS'} <span className="tag">{result.windows[side][0]} ~ {result.windows[side][1]}</span></div>
                      {[
                        ['净交易数', m?.net_n_trades ?? m?.n_trades, null],
                        ['净胜率', m?.net_win_rate, null],
                        ['净PF', m?.net_profit_factor, null],
                        ['净均收益', m?.net_avg_return, null],
                        ['净回撤', m?.net_max_drawdown, null],
                        ['毛交易数', m?.n_trades, null],
                      ].map(([label, val]) => (
                        <div key={String(label)} className="bt-compare-row">
                          <span>{String(label)}</span>
                          <b className="num">{typeof val === 'number' ? fmt(val as number) : '—'}</b>
                        </div>
                      ))}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* 权益曲线 */}
            {equityOption && (
              <div className="card" style={{ marginBottom: 14 }}>
                <div className="h-sec" style={{ marginBottom: 4 }}>
                  <h2 style={{ margin: 0, fontSize: 14 }}>OOS 权益曲线（净成本复利）</h2>
                  <span className="hint">蓝=净值 · 橙虚线=回撤（右轴）</span>
                </div>
                <EChart option={equityOption} height={230} />
              </div>
            )}

            {/* 出场分布 + WF */}
            <div className="two-col" style={{ marginBottom: 14 }}>
              <div className="card">
                <div className="h-sec" style={{ marginBottom: 8 }}><h2 style={{ margin: 0, fontSize: 14 }}>OOS 出场分布</h2></div>
                {exitDist(oosM).map(([k, n]) => (
                  <div key={k} className="bt-bar-row">
                    <span>{EXIT_LABEL[k] || k}</span>
                    <div className="bt-bar"><i style={{ width: `${(n / Math.max(1, oosM?.net_n_trades ?? 1)) * 100}%` }} /></div>
                    <b className="num">{n}</b>
                  </div>
                ))}
                {!exitDist(oosM).length && <div className="muted" style={{ fontSize: 12 }}>无出场数据</div>}
              </div>
              <div className="card">
                <div className="h-sec" style={{ marginBottom: 8 }}><h2 style={{ margin: 0, fontSize: 14 }}>Walk-forward 复核</h2>
                  {wf.wf_pass !== undefined && <span className={`pill ${wf.wf_pass ? 'ok' : 'danger'}`}>{wf.wf_pass ? '通过' : '未通过'}</span>}
                </div>
                {wf.wf_detail?.length ? (
                  <table className="data">
                    <thead><tr><th>窗</th><th className="num">训练PF</th><th className="num">测试PF</th><th className="num">测试DD</th><th className="num">测试胜率</th><th className="num">n</th></tr></thead>
                    <tbody>
                      {wf.wf_detail.map((d) => (
                        <tr key={d.window}>
                          <td>{d.window}</td><td className="num">{fmt(d.train_pf)}</td><td className="num">{fmt(d.test_pf)}</td>
                          <td className="num">{pct(d.test_dd)}</td><td className="num">{pct(d.test_wr)}</td><td className="num">{d.test_n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="muted" style={{ fontSize: 12 }}>{wf.wf_pass === undefined ? '未启用 WF（运行时可勾选）' : '证据不足'}</div>
                )}
              </div>
            </div>

            {/* 基线 */}
            {result.baselines && (
              <div className="card" style={{ marginBottom: 14 }}>
                <div className="h-sec" style={{ marginBottom: 8 }}><h2 style={{ margin: 0, fontSize: 14 }}>基线对比（OOS 同区间）</h2></div>
                <div className="bt-compare">
                  {(['random', 'ma20_60'] as const).map((k) => {
                    const b = (result.baselines || {})[k] as { net_avg_return?: number | null; net_win_rate?: number | null; net_profit_factor?: number | null; n_trades?: number } | undefined
                    if (!b) return null
                    return (
                      <div key={k} className="bt-compare-col">
                        <div className="bt-compare-head">{k === 'random' ? '随机买入基线' : 'MA20/60 基线'}</div>
                        {[['净均收益', b.net_avg_return], ['净胜率', b.net_win_rate], ['净PF', b.net_profit_factor], ['交易数', b.n_trades]].map(([l, v]) => (
                          <div key={String(l)} className="bt-compare-row"><span>{String(l)}</span><b className="num">{typeof v === 'number' ? fmt(v) : '—'}</b></div>
                        ))}
                      </div>
                    )
                  })}
                </div>
                <div className="note">策略净均收益高于两条基线才谈得上 edge；低于基线 = 不如随机。</div>
              </div>
            )}

            {/* 逐笔交易 */}
            <div className="card">
              <div className="h-sec" style={{ marginBottom: 8 }}>
                <h2 style={{ margin: 0, fontSize: 14 }}>OOS 逐笔交易 <span className="tag">{result.oos?.trades?.length ?? 0} 笔（含未成交）</span></h2>
                <span className="hint">点击任意一行查看该笔交易 K 线（箱体/突破/买卖点，可播放）</span>
              </div>
              {selectedTrade && (
                <div className="trade-chart-panel" style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => setSelectedTrade(null)}>收起 ✕</button>
                  </div>
                  <TradeChart trade={selectedTrade} />
                </div>
              )}
              <div style={{ maxHeight: 360, overflow: 'auto' }} className="lab-table-wrap">
                <table className="lab-table">
                  <thead><tr>
                    <th>采样日</th><th>代码</th><th className="num">买入价</th><th>出场</th><th className="num">毛收益</th><th className="num">净收益</th><th className="num">持有日</th><th className="num">佣金</th>
                  </tr></thead>
                  <tbody>
                    {(result.oos?.trades || []).slice(0, 200).map((t: BacktestTrade, i: number) => (
                      <tr
                        key={`${t.ts_code}-${t.signal_date}-${i}`}
                        style={{ cursor: 'pointer' }}
                        className={selectedTrade && selectedTrade.ts_code === t.ts_code && selectedTrade.signal_date === t.signal_date ? 'selected' : ''}
                        onClick={() => setSelectedTrade((prev) =>
                          prev && prev.ts_code === t.ts_code && prev.signal_date === t.signal_date ? null : t)}
                      >
                        <td className="mono">{t.signal_date}</td>
                        <td className="mono">{t.ts_code}</td>
                        <td className="num">{t.entry_price != null ? t.entry_price.toFixed(2) : '—'}</td>
                        <td><span className={`pill ${t.exit === 'stop' ? 'danger' : t.exit === 'bench' ? 'accent' : t.exit === 'target' ? 'ok' : ''}`}>{EXIT_LABEL[t.exit] || t.exit || '未成交'}</span></td>
                        <td className={`num ${t.ret >= 0 ? 'text-ok' : 'text-danger'}`}>{pct(t.ret)}</td>
                        <td className={`num ${(t.net_return ?? 0) >= 0 ? 'text-ok' : 'text-danger'}`}>{t.net_return != null ? pct(t.net_return) : '—'}</td>
                        <td className="num">{t.days ?? '—'}</td>
                        <td className="num">{t.commission != null ? t.commission.toFixed(0) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="note" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <IcoCheck size={13} style={{ color: 'var(--ok-ink)' }} />
                入场 = 突破日下一交易日开盘（ENTRY v1）；成本含佣金/印花税/其他费/滑点。{result.disclaimer}
              </div>
            </div>
          </div>
        )}

        {/* 试验历史 */}
        {history.length > 0 && (
          <div className="card" style={{ marginTop: 14 }}>
            <div className="h-sec" style={{ marginBottom: 8 }}>
              <h2 style={{ margin: 0, fontSize: 14 }}>本机试验历史 <span className="tag">{history.length} 次</span></h2>
              <span className="hint">反复调参跑 OOS 会污染结论——同参数重复试验会标记</span>
            </div>
            <table className="data">
              <thead><tr><th>时间</th><th>参数</th><th className="num">OOS净PF</th><th className="num">净胜率</th><th className="num">净回撤</th><th className="num">交易数</th><th></th></tr></thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={h.ts + i} className="clickable" onClick={() => setForm(h.form)}>
                    <td className="mono" style={{ fontSize: 12 }}>{new Date(h.ts).toLocaleString()}</td>
                    <td>{h.label}</td>
                    <td className={`num ${(h.oosPf ?? 0) >= 1 ? 'text-ok' : 'text-danger'}`}>{fmt(h.oosPf)}</td>
                    <td className="num">{pct(h.oosWr)}</td>
                    <td className="num">{pct(h.oosDd)}</td>
                    <td className="num">{h.oosN}</td>
                    <td><span className="badge badge-mute">点击回填</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
