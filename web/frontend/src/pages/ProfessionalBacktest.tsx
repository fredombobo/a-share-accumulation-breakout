import { useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  api,
  BacktestCatalog,
  BacktestLeaderboardRow,
  BacktestMetrics,
  BacktestParameterDefinition,
  BacktestPreview,
  BacktestRequest,
  BacktestResult,
  BacktestTask,
  BacktestUniverseCatalog,
  ParameterSpec,
} from '../api/client'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'

const ACTIVE_STATUSES: BacktestTask['status'][] = ['pending', 'running', 'cancelling']
const PRIMARY_KEYS = new Set([
  'box_min_days',
  'box_max_days',
  'breakout_vol_ratio',
  'stop_pct',
  'exit_window',
  'strong_reset',
])

const PHASES = [
  ['DATA', '冻结数据'],
  ['GRID', 'IS / OOS 网格'],
  ['WF', '滚动复验'],
  ['BASELINES', '基准对照'],
  ['COST', '成本压力'],
  ['REPORT', '生成结论'],
] as const

function cloneSpec(spec: ParameterSpec): ParameterSpec {
  if (spec.mode === 'fixed') return { mode: 'fixed', value: spec.value }
  if (spec.mode === 'range') return { mode: 'range', start: spec.start, stop: spec.stop, step: spec.step }
  return { mode: 'values', values: [...spec.values] }
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.retryable ? '，可以稍后重试' : ''}`
  if (error instanceof Error) return error.message
  return String(error)
}

function normalizeCodes(raw: string): string[] {
  const values = raw.split(/[\s,，;；]+/).map((value) => value.trim().toUpperCase()).filter(Boolean)
  return [...new Set(values.map((value) => {
    if (!/^\d{6}$/.test(value)) return value
    return value.startsWith('6') ? `${value}.SH` : `${value}.SZ`
  }))]
}

function formatDate(value?: string): string {
  if (!value || value.length !== 8) return value || 'n/a'
  return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}`
}

function formatPercent(value: number | null | undefined): string {
  return value == null ? 'n/a' : `${(value * 100).toFixed(2)}%`
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value == null ? 'n/a' : value.toFixed(digits)
}

function metricRows(metrics: BacktestMetrics | null | undefined) {
  return [
    ['净成交', metrics?.net_n_trades == null ? 'n/a' : `${metrics.net_n_trades} 笔`],
    ['净胜率', formatPercent(metrics?.net_win_rate)],
    ['净 Profit Factor', formatNumber(metrics?.net_profit_factor)],
    ['组合净收益', formatPercent(metrics?.portfolio_total_return)],
    ['组合最大回撤', formatPercent(metrics?.portfolio_max_drawdown)],
  ]
}

function ParameterEditor({
  definition,
  spec,
  onChange,
}: {
  definition: BacktestParameterDefinition
  spec: ParameterSpec
  onChange: (next: ParameterSpec) => void
}) {
  const numberValue = (raw: string) => definition.value_type === 'integer'
    ? Number.parseInt(raw || '0', 10)
    : Number(raw || '0')
  const switchMode = (mode: ParameterSpec['mode']) => {
    const fallback = definition.default
    if (mode === fallback.mode) return onChange(cloneSpec(fallback))
    const first = spec.mode === 'fixed' ? spec.value : spec.mode === 'values' ? spec.values[0] : spec.start
    if (mode === 'fixed') onChange({ mode, value: first ?? (definition.value_type === 'boolean' ? true : 0) })
    else if (mode === 'values') onChange({ mode, values: [first ?? (definition.value_type === 'boolean' ? true : 0)] })
    else onChange({ mode, start: Number(first || 0), stop: Number(first || 0), step: 1 })
  }
  const allowedModes = definition.value_type === 'boolean' ? ['fixed', 'values'] : ['fixed', 'range', 'values']

  return (
    <div className="parameter-editor">
      <div className="parameter-title">
        <label htmlFor={`mode-${definition.key}`}>{definition.title}</label>
        <select
          id={`mode-${definition.key}`}
          value={spec.mode}
          onChange={(event) => switchMode(event.target.value as ParameterSpec['mode'])}
          aria-label={`${definition.title}参数模式`}
        >
          {allowedModes.map((mode) => (
            <option key={mode} value={mode}>{mode === 'fixed' ? '固定' : mode === 'range' ? '范围步进' : '离散值'}</option>
          ))}
        </select>
      </div>
      <p>{definition.description}</p>
      {spec.mode === 'fixed' && definition.value_type === 'boolean' && (
        <select
          className="input"
          value={String(spec.value)}
          onChange={(event) => onChange({ mode: 'fixed', value: event.target.value === 'true' })}
          aria-label={definition.title}
        >
          <option value="true">启用</option>
          <option value="false">关闭</option>
        </select>
      )}
      {spec.mode === 'fixed' && definition.value_type !== 'boolean' && (
        <input
          className="input num"
          type="number"
          min={definition.minimum ?? undefined}
          max={definition.maximum ?? undefined}
          step={definition.value_type === 'integer' ? 1 : 'any'}
          value={String(spec.value)}
          onChange={(event) => onChange({ mode: 'fixed', value: numberValue(event.target.value) })}
          aria-label={definition.title}
        />
      )}
      {spec.mode === 'range' && (
        <div className="parameter-range">
          {(['start', 'stop', 'step'] as const).map((key) => (
            <label key={key}>
              <span>{key === 'start' ? '起点' : key === 'stop' ? '终点' : '步长'}</span>
              <input
                className="input num"
                type="number"
                step={definition.value_type === 'integer' ? 1 : 'any'}
                value={String(spec[key])}
                onChange={(event) => onChange({ ...spec, [key]: numberValue(event.target.value) })}
              />
            </label>
          ))}
        </div>
      )}
      {spec.mode === 'values' && (
        <input
          className="input num"
          value={spec.values.map(String).join(', ')}
          onChange={(event) => {
            const parts = event.target.value.split(/[,，\s]+/).filter(Boolean)
            const values = definition.value_type === 'boolean'
              ? parts.map((value) => value.toLowerCase() === 'true')
              : parts.map(numberValue)
            onChange({ mode: 'values', values })
          }}
          placeholder={definition.value_type === 'boolean' ? 'true, false' : '例如 1.4, 1.6, 1.8'}
          aria-label={`${definition.title}离散值`}
        />
      )}
      {definition.minimum != null && (
        <small>允许范围 {definition.minimum} 至 {definition.maximum}</small>
      )}
    </div>
  )
}

function ResultMetrics({ title, metrics }: { title: string; metrics: BacktestMetrics | null | undefined }) {
  return (
    <section className="metric-compare-block">
      <h4>{title}</h4>
      {metricRows(metrics).map(([label, value]) => (
        <div className="stat" key={label}><span className="k">{label}</span><span className="v">{value}</span></div>
      ))}
    </section>
  )
}

function BacktestResultView({ result }: { result: BacktestResult }) {
  const selected = result.selected
  const verdictClass = result.verdict === 'EXPLORATORY_PROMISING' ? 'ok' : 'warn'
  const baselineEntries = Object.entries(result.baselines || {})
  const downloadReport = () => {
    const blob = new Blob([result.report_markdown || '报告内容不可用'], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `ab-professional-backtest-${result.request.input_hash.slice(0, 10)}.md`
    link.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div className="backtest-result" aria-live="polite">
      <section className={`result-verdict ${verdictClass}`}>
        <div>
          <span className="guide-eyebrow">探索性结论</span>
          <h2>{result.verdict_label}</h2>
          <p>本结果不会自动改变每日选股，也不能直接晋级生产参数。</p>
        </div>
        {result.report_markdown && <button className="btn" type="button" onClick={downloadReport}>下载 Markdown 报告</button>}
      </section>
      <div className="result-reasons">
        {(result.verdict_reasons || []).map((reason) => <div key={reason}>检查项：{reason}</div>)}
        {!result.candidate_eligible && <div>晋级状态：未晋级。需要另行预登记后复验。</div>}
      </div>
      {selected ? (
        <>
          <div className="metric-compare">
            <ResultMetrics title="IS 样本内" metrics={selected.is} />
            <ResultMetrics title="OOS 样本外" metrics={selected.oos} />
            <ResultMetrics title="2 倍成本压力" metrics={result.cost_stress?.metrics} />
          </div>
          <section className="card section-gap">
            <div className="h-sec"><h2>基准与滚动窗口</h2></div>
            <div className="baseline-grid">
              {baselineEntries.map(([name, metrics]) => (
                <ResultMetrics key={name} title={name === 'random' ? '固定种子随机基线' : 'MA20 / MA60 基线'} metrics={metrics} />
              ))}
              <div className="metric-compare-block">
                <h4>WF 滚动复验</h4>
                <div className="stat"><span className="k">证据完整</span><span className="v">{result.wf?.evidence_complete ? '是' : '否'}</span></div>
                <div className="stat"><span className="k">稳定性通过</span><span className="v">{result.wf?.wf_pass ? '是' : '否'}</span></div>
                <div className="stat"><span className="k">窗口平均 PF</span><span className="v">{formatNumber(result.wf?.oos_mean_pf)}</span></div>
              </div>
            </div>
          </section>
          <section className="card section-gap">
            <div className="h-sec">
              <h2>入选参数与排行榜</h2>
              <span className="pill">已评估 {result.evaluated_combinations ?? result.leaderboard.length} 组</span>
            </div>
            <div className="selected-params">
              <code>{JSON.stringify(selected.signal)}</code>
              <code>{JSON.stringify(selected.exit)}</code>
            </div>
            <Leaderboard rows={result.leaderboard.slice(0, 10)} />
          </section>
        </>
      ) : <div className="empty section-gap"><strong>没有可入选组合</strong>当前样本没有达到最低成交证据要求。</div>}
      <details className="backtest-warnings section-gap">
        <summary>研究边界与数据说明</summary>
        <ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      </details>
    </div>
  )
}

function Leaderboard({ rows }: { rows: BacktestLeaderboardRow[] }) {
  if (!rows.length) return <div className="empty">暂无排行榜数据</div>
  return (
    <div className="table-scroll">
      <table className="data">
        <thead><tr><th>排名</th><th>横盘最长</th><th>突破量比</th><th>止损</th><th>退出窗</th><th>IS PF</th><th>OOS PF</th><th>OOS 净收益</th><th>OOS 成交</th></tr></thead>
        <tbody>{rows.map((row, index) => (
          <tr key={row.param_id}>
            <td className="num">{index + 1}</td>
            <td className="num">{String(row.signal.box_max_days)}</td>
            <td className="num">{String(row.signal.breakout_vol_ratio)}</td>
            <td className="num">{formatPercent(Number(row.exit.stop_pct))}</td>
            <td className="num">{String(row.exit.exit_window)}</td>
            <td className="num">{formatNumber(row.is.net_profit_factor)}</td>
            <td className="num">{formatNumber(row.oos.net_profit_factor)}</td>
            <td className="num">{formatPercent(row.oos.portfolio_total_return)}</td>
            <td className="num">{row.oos.net_n_trades ?? 0}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export default function ProfessionalBacktest() {
  const [catalog, setCatalog] = useState<BacktestCatalog | null>(null)
  const [universe, setUniverse] = useState<BacktestUniverseCatalog | null>(null)
  const [parameters, setParameters] = useState<Record<string, ParameterSpec>>({})
  const [industries, setIndustries] = useState<string[]>([])
  const [industryFilter, setIndustryFilter] = useState('')
  const [codesText, setCodesText] = useState('')
  const [maxCodes, setMaxCodes] = useState(600)
  const [sampleStep, setSampleStep] = useState(10)
  const [conditionFlags, setConditionFlags] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<BacktestPreview | null>(null)
  const [task, setTask] = useState<BacktestTask | null>(null)
  const [busy, setBusy] = useState<'load' | 'preview' | 'run' | 'cancel' | ''>('load')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([api.backtestCatalog(), api.backtestUniverse(), api.backtestLatest()])
      .then(([nextCatalog, nextUniverse, latest]) => {
        if (!active) return
        setCatalog(nextCatalog)
        setUniverse(nextUniverse)
        setParameters(Object.fromEntries(nextCatalog.parameters.map((item) => [item.key, cloneSpec(item.default)])))
        setConditionFlags(Object.fromEntries(nextCatalog.conditions.map((item) => [item.id, item.default_enabled])))
        setTask(latest.task)
      })
      .catch((reason) => active && setError(errorMessage(reason)))
      .finally(() => active && setBusy(''))
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!task || !ACTIVE_STATUSES.includes(task.status)) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const refresh = async () => {
      try {
        const next = await api.backtestStatus(task.task_id)
        if (active) setTask(next)
      } catch (reason) {
        if (active) setError(errorMessage(reason))
      } finally {
        if (active) timer = setTimeout(refresh, 1500)
      }
    }
    const onFocus = () => void refresh()
    const onVisibility = () => { if (document.visibilityState === 'visible') void refresh() }
    timer = setTimeout(refresh, 900)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      active = false
      if (timer) clearTimeout(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [task?.task_id, task?.status])

  const request = useMemo<BacktestRequest>(() => ({
    strategy: 'A',
    sample_step: sampleStep,
    max_codes: maxCodes,
    parameters,
    universe: { industries, codes: normalizeCodes(codesText) },
    conditions: (catalog?.conditions || []).map((condition) => ({
      id: condition.id,
      enabled: Boolean(conditionFlags[condition.id]),
      params: {},
    })),
    windows: { mode: 'auto' },
  }), [catalog, codesText, conditionFlags, industries, maxCodes, parameters, sampleStep])

  const mutate = (action: () => void) => {
    action()
    setPreview(null)
    setError('')
  }

  const handlePreview = async () => {
    setBusy('preview')
    setError('')
    try {
      setPreview(await api.backtestPreview(request))
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy('')
    }
  }

  const handleRun = async () => {
    if (!preview) return
    const confirmed = window.confirm(`将运行 ${preview.prepared.parameter_space.count} 组参数，冻结 ${preview.prepared.universe.count} 只股票。是否继续？`)
    if (!confirmed) return
    setBusy('run')
    setError('')
    try {
      const started = await api.backtestRun(request)
      window.dispatchEvent(new Event(RUN_TASK_EVENT))
      setTask(await api.backtestStatus(started.task_id))
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'BACKTEST_ALREADY_RUNNING' && typeof reason.details.task_id === 'string') {
        window.dispatchEvent(new Event(RUN_TASK_EVENT))
        setTask(await api.backtestStatus(reason.details.task_id))
      } else {
        setError(errorMessage(reason))
      }
    } finally {
      setBusy('')
    }
  }

  const handleCancel = async () => {
    if (!task || !window.confirm('取消当前专业回测？已完成的运行记录会保留。')) return
    setBusy('cancel')
    try {
      setTask(await api.backtestCancel(task.task_id))
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy('')
    }
  }

  const visibleIndustries = (universe?.industries || []).filter((item) => item.name.includes(industryFilter)).slice(0, 80)
  const activeTask = task && ACTIVE_STATUSES.includes(task.status)

  if (busy === 'load') return <div className="loading">正在读取专业回测契约和本地股票池...</div>
  if (!catalog || !universe) return <div className="err">专业回测初始化失败：{error || '接口不可用'}</div>

  return (
    <div className="backtest-shell fade-up">
      <section className="backtest-intro">
        <div>
          <span className="guide-eyebrow">AB 横盘吸筹突破</span>
          <h1>多参数专业回测</h1>
          <p>一次冻结参数空间和股票池。只用 IS 选参，再用 OOS、滚动窗口、基线与成本压力核验。</p>
        </div>
        <div className="research-boundary">
          <b>研究边界</b>
          <span>探索结果不自动进入每日选股</span>
          <span>不连接券商，不生成订单</span>
        </div>
      </section>

      {error && <div className="guide-feedback error" role="alert"><b>无法继续</b><span>{error}</span></div>}

      {task && (
        <section className={`task-status ${task.status}`} aria-live="polite">
          <div className="task-status-head">
            <div>
              <span className="guide-eyebrow">最近研究任务</span>
              <h2>{task.status === 'done' ? '回测已完成' : task.status === 'error' ? '回测失败' : task.message || '回测运行中'}</h2>
              <p className="mono">{task.task_id} · {task.progress}% · {task.message}</p>
            </div>
            {activeTask && <button className="btn danger" type="button" onClick={handleCancel} disabled={busy === 'cancel'}>取消任务</button>}
          </div>
          <ol className="stage-strip">
            {PHASES.map(([phase, label], index) => {
              const currentIndex = PHASES.findIndex(([item]) => item === task.phase)
              const done = task.status === 'done' || currentIndex > index
              const current = currentIndex === index && activeTask
              return <li key={phase} className={done ? 'done' : current ? 'current' : ''}><span>{done ? '✓' : index + 1}</span>{label}</li>
            })}
          </ol>
        </section>
      )}

      <div className="backtest-layout">
        <aside className="backtest-config">
          <section>
            <div className="config-heading"><h2>1. 选择股票池</h2><span>{industries.length ? `${industries.length} 个板块` : '全市场'}</span></div>
            <p className="config-note">板块来自当前分类，运行前会冻结具体股票代码与哈希。</p>
            <input className="input" value={industryFilter} onChange={(event) => setIndustryFilter(event.target.value)} placeholder="筛选板块名称" aria-label="筛选板块名称" />
            <div className="industry-actions">
              <button className="btn btn-sm" type="button" onClick={() => mutate(() => setIndustries(visibleIndustries.map((item) => item.name)))}>勾选当前结果</button>
              <button className="btn btn-sm" type="button" onClick={() => mutate(() => setIndustries([]))}>清空</button>
            </div>
            <div className="industry-picker" role="group" aria-label="回测板块">
              {visibleIndustries.map((item) => (
                <label key={item.name}>
                  <input
                    type="checkbox"
                    checked={industries.includes(item.name)}
                    onChange={() => mutate(() => setIndustries((current) => current.includes(item.name) ? current.filter((value) => value !== item.name) : [...current, item.name]))}
                  />
                  <span>{item.name}</span><small>{item.count}</small>
                </label>
              ))}
            </div>
            <div className="field section-gap">
              <label htmlFor="explicit-codes">指定股票代码，可选</label>
              <textarea id="explicit-codes" className="input codes-input" value={codesText} onChange={(event) => mutate(() => setCodesText(event.target.value))} placeholder="000001, 600000。填写后优先于板块。" />
            </div>
            <div className="compact-fields">
              <div className="field"><label htmlFor="max-codes">最多股票数</label><input id="max-codes" className="input num" type="number" min="20" max="1500" value={maxCodes} onChange={(event) => mutate(() => setMaxCodes(Number(event.target.value)))} /></div>
              <div className="field">
                <label htmlFor="sample-step">交易日采样间隔</label>
                <input id="sample-step" className="input num" type="number" min="1" max="60" value={sampleStep} onChange={(event) => mutate(() => setSampleStep(Number(event.target.value)))} />
                <small>每隔 N 个交易日生成一个研究决策截面；越小越慢，默认 10。</small>
              </div>
            </div>
          </section>
          <section className="condition-section">
            <div className="config-heading"><h2>筹码条件扩展口</h2><span>预留</span></div>
            {(catalog.conditions || []).map((condition) => (
              <label className="condition-row" key={condition.id}>
                <input type="checkbox" checked={Boolean(conditionFlags[condition.id])} disabled={!condition.production_ready} onChange={(event) => mutate(() => setConditionFlags((current) => ({ ...current, [condition.id]: event.target.checked })))} />
                <span><b>{condition.title}</b><small>{condition.status}。数据 {condition.dataset?.available ? `${condition.dataset.rows} 行` : '未就绪'}。</small></span>
              </label>
            ))}
          </section>
        </aside>

        <main className="backtest-workspace">
          <section className="card">
            <div className="h-sec"><h2>2. 设置参数空间</h2><span className="pill">上限 {catalog.max_combinations} 组</span></div>
            <div className="parameter-grid">
              {catalog.parameters.filter((item) => PRIMARY_KEYS.has(item.key)).map((definition) => (
                <ParameterEditor key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
              ))}
            </div>
            <details className="advanced-parameters">
              <summary>其余信号条件与约束</summary>
              <div className="parameter-grid">
                {catalog.parameters.filter((item) => !PRIMARY_KEYS.has(item.key)).map((definition) => (
                  <ParameterEditor key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
                ))}
              </div>
            </details>
          </section>

          <section className="run-console section-gap">
            <div>
              <span className="guide-eyebrow">3. 先核对，再运行</span>
              <h2>{preview ? '回测输入已冻结预览' : '尚未生成运行预览'}</h2>
              <p>{preview ? preview.estimated_work.note : '调整参数或股票池后必须重新预览，防止误跑超大参数空间。'}</p>
            </div>
            <div className="run-actions">
              <button className="btn" type="button" onClick={handlePreview} disabled={busy === 'preview' || Boolean(activeTask)}>{busy === 'preview' ? '检查中...' : '检查参数空间'}</button>
              <button className="btn primary" type="button" onClick={handleRun} disabled={!preview || busy === 'run' || Boolean(activeTask)}>{busy === 'run' ? '正在启动...' : '启动专业回测'}</button>
            </div>
          </section>

          {preview && (
            <section className="preview-grid" aria-label="回测预览">
              <div><span>有效组合</span><b>{preview.prepared.parameter_space.count}</b></div>
              <div><span>冻结股票</span><b>{preview.prepared.universe.count}</b></div>
              <div><span>动态预热</span><b>{preview.prepared.parameter_space.horizon} 日</b></div>
              <div><span>研究窗口</span><b>{formatDate(preview.prepared.windows.is[0])} 至 {formatDate(preview.prepared.windows.oos[1])}</b></div>
              <p>{preview.prepared.universe.classification_note}</p>
            </section>
          )}

          {task?.result && <BacktestResultView result={task.result} />}
          {!task?.result && !activeTask && (
            <div className="empty section-gap"><strong>等待一次专业回测</strong>默认参数会搜索横盘 60 至 200 天，并核验突破量比、止损和退出窗口。</div>
          )}
        </main>
      </div>
    </div>
  )
}
