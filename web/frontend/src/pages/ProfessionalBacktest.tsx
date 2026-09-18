import { useEffect, useMemo, useRef, useState } from 'react'
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
  ClassificationKey,
  ParameterSpec,
  ProfileActivation,
  EntryCopyStatus,
  StrategyProfileState,
} from '../api/client'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'
import BacktestResultCharts from '../components/BacktestResultCharts'
import {
  portfolioMaxDrawdown,
  portfolioTotalReturn,
} from '../components/backtestMetricContract'
import ParameterCheckDialog, { type ParameterCheckResult } from '../components/ParameterCheckDialog'
import BacktestAccountDetails from '../components/BacktestAccountDetails'
import BacktestMarketComparison from '../components/BacktestMarketComparison'
import ReplayCorrectionPanel from '../components/ReplayCorrectionPanel'
import './ProfessionalBacktest.css'

const ACTIVE_STATUSES: BacktestTask['status'][] = ['pending', 'running', 'cancelling']
const PRIMARY_ENTRY_KEYS = new Set([
  'box_min_days',
  'box_max_days',
  'breakout_vol_ratio',
])
const EXIT_KEYS = new Set(['stop_pct', 'target_pct', 'max_hold_days', 'vol_ratio_min', 'exit_window', 'strong_reset'])
const ADVANCED_EXIT_KEYS = new Set(['vol_ratio_min', 'exit_window', 'strong_reset'])

const PHASES = [
  ['DATA', '冻结数据'],
  ['GRID', 'IS / OOS 网格'],
  ['WF', '滚动复验'],
  ['BASELINES', '基准对照'],
  ['COST', '成本压力'],
  ['DETAILS', '账户明细'],
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

const PARAMETER_LABELS: Record<string, string> = {
  box_min_days: '横盘最短',
  box_max_days: '横盘最长',
  box_max_amp: '箱体振幅上限',
  breakout_vol_ratio: '突破量比',
  breakout_chg_min: '突破涨幅下限',
  breakout_chg_max: '突破涨幅上限',
  breakout_vs_recent_vol_ratio: '近期放量倍数',
  breakout_window_days: '突破确认窗',
  require_structure: '结构要求',
  vol_ratio_min: '建仓量比',
  stop_pct: '止损',
  target_pct: '止盈',
  max_hold_days: '最长持有',
  exit_window: '二次出货观察窗',
  strong_reset: '强势重置',
}

const PERCENT_PARAMETER_KEYS = new Set(['box_max_amp', 'breakout_chg_min', 'breakout_chg_max', 'stop_pct', 'target_pct'])
const DAY_PARAMETER_KEYS = new Set(['box_min_days', 'box_max_days', 'breakout_window_days', 'max_hold_days', 'exit_window'])

function formatParameterValue(key: string, value: number | boolean): string {
  if (typeof value === 'boolean') return value ? '启用' : '关闭'
  if (PERCENT_PARAMETER_KEYS.has(key)) return formatPercent(value)
  if (DAY_PARAMETER_KEYS.has(key)) return `${value} 日`
  return String(value)
}

function metricRows(metrics: BacktestMetrics | null | undefined) {
  const totalReturn = portfolioTotalReturn(metrics)
  const maxDrawdown = portfolioMaxDrawdown(metrics)
  return [
    ['组合净收益', formatPercent(totalReturn)],
    ['组合最大回撤', formatPercent(maxDrawdown)],
    ['净成交', metrics?.net_n_trades == null ? 'n/a' : `${metrics.net_n_trades} 笔`],
    ['净胜率', formatPercent(metrics?.net_win_rate)],
    ['净 Profit Factor', formatNumber(metrics?.net_profit_factor)],
  ]
}

function ParameterEditor({
  definition,
  spec,
  onChange,
  percentage = false,
}: {
  definition: BacktestParameterDefinition
  spec: ParameterSpec
  onChange: (next: ParameterSpec) => void
  percentage?: boolean
}) {
  const numberValue = (raw: string) => {
    const parsed = definition.value_type === 'integer'
      ? Number.parseInt(raw || '0', 10)
      : Number(raw || '0')
    return percentage ? parsed / 100 : parsed
  }
  const displayValue = (value: number | boolean) => (
    typeof value === 'number' && percentage ? Number((value * 100).toFixed(8)) : value
  )
  const switchMode = (mode: ParameterSpec['mode']) => {
    const fallback = definition.default
    if (mode === fallback.mode) return onChange(cloneSpec(fallback))
    const first = spec.mode === 'fixed' ? spec.value : spec.mode === 'values' ? spec.values[0] : spec.start
    if (mode === 'fixed') onChange({ mode, value: first ?? (definition.value_type === 'boolean' ? true : 0) })
    else if (mode === 'values') onChange({ mode, values: [first ?? (definition.value_type === 'boolean' ? true : 0)] })
    else onChange({ mode, start: Number(first || 0), stop: Number(first || 0), step: percentage ? 0.01 : 1 })
  }
  const allowedModes = definition.value_type === 'boolean' ? ['fixed', 'values'] : ['fixed', 'range', 'values']

  return (
    <div className="parameter-editor" data-parameter={definition.key}>
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
      <p id={`description-${definition.key}`}>{definition.description}</p>
      {spec.mode === 'fixed' && definition.value_type === 'boolean' && (
        <select
          className="input"
          value={String(spec.value)}
          onChange={(event) => onChange({ mode: 'fixed', value: event.target.value === 'true' })}
          aria-label={definition.title}
          aria-describedby={`description-${definition.key}`}
        >
          <option value="true">启用</option>
          <option value="false">关闭</option>
        </select>
      )}
      {spec.mode === 'fixed' && definition.value_type !== 'boolean' && (
        <input
          className="input num"
          type="number"
          min={definition.minimum == null ? undefined : Number(displayValue(definition.minimum))}
          max={definition.maximum == null ? undefined : Number(displayValue(definition.maximum))}
          step={definition.value_type === 'integer' ? 1 : 'any'}
          value={String(displayValue(spec.value))}
          onChange={(event) => onChange({ mode: 'fixed', value: numberValue(event.target.value) })}
          aria-label={definition.title}
          aria-describedby={`description-${definition.key}`}
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
                value={String(displayValue(spec[key]))}
                onChange={(event) => onChange({ ...spec, [key]: numberValue(event.target.value) })}
                aria-label={`${definition.title}${key === 'start' ? '起点' : key === 'stop' ? '终点' : '步长'}`}
              />
            </label>
          ))}
        </div>
      )}
      {spec.mode === 'values' && (
        <input
          className="input num"
          value={spec.values.map((value) => String(displayValue(value))).join(', ')}
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
        <small>
          允许范围 {String(displayValue(definition.minimum))} 至 {String(displayValue(definition.maximum!))}{percentage ? '%' : ''}
        </small>
      )}
    </div>
  )
}

function ResultMetrics({ title, metrics }: { title: string; metrics: BacktestMetrics | null | undefined }) {
  const [primary, ...supporting] = metricRows(metrics)
  return (
    <section className="metric-compare-block">
      <h4>{title}</h4>
      <div className="research-primary-metric"><span>{primary[0]}</span><strong>{primary[1]}</strong></div>
      {supporting.map(([label, value]) => (
        <div className="stat" key={label}><span className="k">{label}</span><span className="v">{value}</span></div>
      ))}
    </section>
  )
}

function BacktestResultView({
  result,
  reportIdentity,
  activation,
  activeProfile,
  activating,
  onActivate,
  entryCopy,
  onCopyEntry,
}: {
  result: BacktestResult
  reportIdentity?: { taskId: string; codeVersion: string }
  activation?: ProfileActivation
  activeProfile: StrategyProfileState | null
  activating: boolean
  onActivate: () => void
  entryCopy?: EntryCopyStatus
  onCopyEntry: () => void
}) {
  const selected = result.selected
  const verdictClass = ['EXPLORATORY_PROMISING', 'HISTORICAL_SUPPORT_ONLY'].includes(result.verdict) ? 'ok' : 'warn'
  const baselineEntries = Object.entries(result.baselines || {})
  const hasIndependentPaths = Boolean(
    result.path_analysis?.evidence_complete && result.independent_leaderboard?.length,
  )
  const excludedPaths = result.path_analysis?.excluded_without_complete_path ?? 0
  const entryMechanism = result.request.entry_mechanism
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
    <div className="backtest-result research-report" aria-live="polite" aria-label="研究回测报告">
      <header className="research-report-heading">
        <div><span className="guide-eyebrow">研究结果</span><h2>回测报告</h2></div>
        <div className="research-report-meta">
          <span>{result.request.universe.count} 只股票</span>
          <span>{result.evaluated_combinations ?? result.leaderboard.length} 组参数</span>
          <span>{formatDate(result.request.windows.is[0])} — {formatDate(result.request.windows.oos[1])}</span>
        </div>
      </header>
      <section className={`result-verdict ${verdictClass}`}>
        <div>
          <span className="guide-eyebrow">探索性结论</span>
          <h2>{result.verdict_label}</h2>
          <p>本结果不会自动改变每日研究扫描，也不能直接晋级生产参数。{result.request.windows.mode !== 'full' && '当前不是完整验证窗，仅供摸底研究，不可宣称已经验证有效。'}</p>
        </div>
        {result.report_markdown && <button className="btn" type="button" onClick={downloadReport}>下载 Markdown 报告</button>}
      </section>
      {selected && (
        <div className="metric-compare research-key-metrics" aria-label="回测核心指标">
          <ResultMetrics title="OOS 样本外" metrics={selected.oos} />
          <ResultMetrics title="IS 样本内" metrics={selected.is} />
          <ResultMetrics title="2 倍成本压力" metrics={result.cost_stress?.metrics} />
        </div>
      )}
      <div className="result-reasons">
        {(result.verdict_reasons || []).map((reason) => <div key={reason}>检查项：{reason}</div>)}
        {!result.candidate_eligible && <div>晋级状态：未晋级。需要另行预登记后复验。</div>}
      </div>
      {entryMechanism?.research_only && (
        <div className="guide-feedback warning" role="note">
          <b>预登记研究机制：{entryMechanism.id}</b>
          <span>
            版本 {entryMechanism.version} · 语义 {entryMechanism.semantic_hash}。历史通过也只构成历史支持，不能启用为今日选股参数。
          </span>
          {entryMechanism.id === 'POST_BREAKOUT_SUPPLY_DRY_UP_V1' && (
            <span>时点：t0 严格突破 → t1 下一交易日收盘确认 → 最早 t2 开盘模拟成交。</span>
          )}
          {entryMechanism.id === 'INTERMEDIATE_MOMENTUM_SKIP_MONTH_V1' && (
            <span>中期动量：跳过最近一个月，在冻结股票池中仅接受中期表现前 30% 的严格突破。固定规则，不按结果再次调参。</span>
          )}
        </div>
      )}
      {result.request.sample_step > 1 && (
        <div className="guide-feedback warning" role="note">
          <b>这是抽样研究，不是逐日完整回测</b>
          <span>当前每 {result.request.sample_step} 个交易日取一个决策截面；正式逐日一致性复验应使用步长 1。</span>
        </div>
      )}
      {result.request.sample_step === 1 && (
        <div className="guide-feedback success" role="note">
          <b>逐交易日完整复验</b>
          <span>每个交易日都检查一次因果信号，不使用抽样间隔。</span>
        </div>
      )}
      <section className={`profile-promotion-card ${activation?.can_activate ? 'eligible' : 'blocked'}`}>
        <div>
          <span className="guide-eyebrow">回测 → 今日选股</span>
          <h3>
            {activation?.already_active
              ? '这组参数已用于今日 A 池扫描'
              : activation?.can_activate
                ? '证据门槛通过，可人工启用'
                : '当前结果不能作为回测档案启用'}
          </h3>
          <p>
            {activation?.boundary.notice
              || '只统一 A 池技术入场参数；资金、基本面和市场环境门禁仍会继续执行。'}
          </p>
          <small>复制只复用入场条件。新筛选配置不会继承这份完整回测的收益与验证状态。</small>
          {!activation?.can_activate && (activation?.reasons || []).slice(0, 3).map((item) => (
            <div className="profile-check-fail" key={item.code}>{item.label}：{item.message}</div>
          ))}
          {activation?.already_active && activeProfile && (
            <small className="mono">当前版本 {activeProfile.active.version} · {activeProfile.active.config_hash}</small>
          )}
        </div>
        {activation?.can_activate && !activation.already_active && (
          <button className="btn primary" type="button" disabled={activating} onClick={onActivate}>
            {activating ? '正在启用...' : '人工启用为今日选股参数'}
          </button>
        )}
      </section>
      {selected && <section className="research-entry-copy"><div><h3>将入场条件带回每日筛选</h3><p>只复制九项入场条件，退出规则由当前研究档案保留。</p>{!entryCopy?.can_copy && <small>{entryCopy?.reasons?.map(reason => reason.message).join('；') || '此报告尚未提供可复制的每日入场条件。'}</small>}</div><button className="btn" type="button" disabled={!entryCopy?.can_copy || activating || !activeProfile} onClick={onCopyEntry}>{activating ? '正在处理…' : '复制入场条件到每日筛选'}</button></section>}
      <BacktestMarketComparison comparison={result.market_comparison} />
      {selected ? (
        <>
          <BacktestResultCharts result={result} reportIdentity={reportIdentity} />
          <BacktestAccountDetails result={result} />
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
                {(result.wf?.wf_detail || []).map((row, index) => (
                  <div className="stat" key={row.window || index}>
                    <span className="k">{row.window || `WF${index + 1}`} 测试成交</span>
                    <span className="v">{row.test_n === 0 ? (row.test_diagnostic ? '0 笔（见样本诊断）' : '旧记录 0（过滤前未记录）') : row.test_n == null ? '未记录' : `${row.test_n} 笔`}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>
          <section className="card section-gap">
            <div className="h-sec">
              <h2>{hasIndependentPaths ? '入选参数与独立路径排行榜' : '入选参数与历史名义排行榜'}</h2>
              <span className="pill">
                名义 {result.path_analysis?.nominal_combinations ?? result.evaluated_combinations ?? result.leaderboard.length} 组
                {result.path_analysis?.evidence_complete && result.path_analysis.path_eligible_combinations != null
                  ? ` · 可验证 ${result.path_analysis.path_eligible_combinations}`
                  : ''}
                {result.path_analysis?.evidence_complete && result.path_analysis.independent_joint_paths != null
                  ? ` · 独立路径 ${result.path_analysis.independent_joint_paths}`
                  : ''}
              </span>
            </div>
            <div className="selected-parameter-grid" aria-label="入选参数摘要">
              {[...Object.entries(selected.signal), ...Object.entries(selected.exit)].map(([key, value]) => (
                <div key={key}>
                  <span>{PARAMETER_LABELS[key] || key}</span>
                  <b>{formatParameterValue(key, value)}</b>
                </div>
              ))}
            </div>
            <details className="selected-technical-detail">
              <summary>查看原始参数与技术身份</summary>
              <code>{JSON.stringify({ signal: selected.signal, exit: selected.exit })}</code>
              <code>参数 ID: {selected.param_id} · 输入: {result.request.input_hash}</code>
            </details>
            {hasIndependentPaths && excludedPaths > 0 && (
              <div className="guide-feedback warning" role="note">
                <b>{excludedPaths} 组未进入路径比较</b>
                <span>这些名义参数缺少完整 IS+OOS 权益路径，通常是净成交不足；系统没有把它们猜成独立或等效路径。</span>
              </div>
            )}
            {!hasIndependentPaths && (
              <div className="guide-feedback warning" role="note">
                <b>结果缺少可验证权益路径，排行榜未去重</b>
              <span>保留原始名义参数结果，不根据相同的四舍五入指标猜测它们是否等效；请以 v1.5 新任务复验。</span>
              </div>
            )}
            <Leaderboard
              rows={(hasIndependentPaths ? result.independent_leaderboard! : result.leaderboard).slice(0, 10)}
              hasIndependentPaths={hasIndependentPaths}
            />
          </section>
        </>
      ) : <div className="research-empty"><span className="guide-eyebrow">样本证据不足</span><strong>没有可入选组合</strong><p>当前样本没有达到最低成交证据要求。可查看下方研究边界，核对股票范围与参数设置。</p></div>}
      <details className="backtest-warnings section-gap">
        <summary>研究边界与数据说明</summary>
        <ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      </details>
    </div>
  )
}

function Leaderboard({
  rows,
  hasIndependentPaths,
}: {
  rows: BacktestLeaderboardRow[]
  hasIndependentPaths: boolean
}) {
  if (!rows.length) return <div className="empty">暂无排行榜数据</div>
  return (
    <div className="table-scroll research-leaderboard" tabIndex={0} role="region" aria-label="参数排行榜，可横向滚动">
      <table className="data">
        <caption>按样本内结果选择参数，列出前十项的样本外表现。</caption>
        <thead><tr><th>排名</th><th>{hasIndependentPaths ? '等效参数' : '路径证据'}</th><th>横盘最长</th><th>突破量比</th><th>止损</th><th>止盈</th><th>最长持有</th><th>二次出货窗</th><th>IS PF</th><th>OOS PF</th><th>OOS 净收益</th><th>OOS 成交</th></tr></thead>
        <tbody>{rows.map((row, index) => (
          <tr key={row.param_id}>
            <td className="num">{index + 1}</td>
            <td className="num">{hasIndependentPaths ? `${row.equivalent_parameter_count ?? 1} 组` : '未去重'}</td>
            <td className="num">{String(row.signal.box_max_days)}</td>
            <td className="num">{String(row.signal.breakout_vol_ratio)}</td>
            <td className="num">{formatPercent(Number(row.exit.stop_pct))}</td>
            <td className="num">{formatPercent(Number(row.exit.target_pct))}</td>
            <td className="num">{String(row.exit.max_hold_days ?? 30)}</td>
            <td className="num">{String(row.exit.exit_window)}</td>
            <td className="num">{formatNumber(row.is.net_profit_factor)}</td>
            <td className="num">{formatNumber(row.oos.net_profit_factor)}</td>
            <td className="num">{formatPercent(portfolioTotalReturn(row.oos))}</td>
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
  const [classification, setClassification] = useState<ClassificationKey>('industry')
  const [groups, setGroups] = useState<string[]>([])
  const [groupFilter, setGroupFilter] = useState('')
  const [universeBusy, setUniverseBusy] = useState(false)
  const [codesText, setCodesText] = useState('')
  const [maxCodes, setMaxCodes] = useState(600)
  const [sampleStep, setSampleStep] = useState(10)
  const [conditionFlags, setConditionFlags] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<BacktestPreview | null>(null)
  const [checkDialog, setCheckDialog] = useState<ParameterCheckResult | null>(null)
  const [task, setTask] = useState<BacktestTask | null>(null)
  const [profileState, setProfileState] = useState<StrategyProfileState | null>(null)
  const [profileFeedback, setProfileFeedback] = useState('')
  const [busy, setBusy] = useState<'load' | 'preview' | 'run' | 'cancel' | ''>('load')
  const [error, setError] = useState('')
  const previewRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    let active = true
    Promise.all([api.backtestCatalog(), api.backtestUniverse(), api.backtestLatest()])
      .then(([nextCatalog, nextUniverse, latest]) => {
        if (!active) return
        setCatalog(nextCatalog)
        setUniverse(nextUniverse)
        setParameters(Object.fromEntries(nextCatalog.parameters.map((item) => [item.key, cloneSpec(item.default)])))
        setConditionFlags(Object.fromEntries(nextCatalog.conditions.map((item) => [item.id, item.default_enabled])))
        setTask(latest.task ? { ...latest.task, profile_activation: latest.profile_activation, entry_copy: latest.entry_copy ?? latest.task.entry_copy } : null)
      })
      .catch((reason) => active && setError(errorMessage(reason)))
      .finally(() => active && setBusy(''))
    api.backtestProfile()
      .then((nextProfile) => {
        if (!nextProfile?.active?.entry || !nextProfile?.boundary) return
        if (active) setProfileState(nextProfile)
      })
      .catch(() => undefined)
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
    universe: { classification, groups, codes: normalizeCodes(codesText) },
    conditions: (catalog?.conditions || []).map((condition) => ({
      id: condition.id,
      enabled: Boolean(conditionFlags[condition.id]),
      params: {},
    })),
    windows: { mode: 'auto' },
  }), [catalog, classification, codesText, conditionFlags, groups, maxCodes, parameters, sampleStep])

  const mutate = (action: () => void) => {
    action()
    setPreview(null)
    setCheckDialog(null)
    setError('')
  }

  const handlePreview = async () => {
    setBusy('preview')
    setError('')
    try {
      const next = await api.backtestPreview(request)
      setPreview(next)
      setCheckDialog(next.can_run ? { kind: 'success', preview: next } : {
        kind: 'error', message: next.prepared.data_scope?.issues.join('；') || '研究及预热数据未通过检查',
      })
    } catch (reason) {
      const message = errorMessage(reason)
      setError(message)
      setCheckDialog({ kind: 'error', message })
    } finally {
      setBusy('')
    }
  }

  const handleRun = async () => {
    if (!preview?.can_run) return
    const count = preview.prepared.parameter_space.count
    const confirmed = window.confirm(
      preview.estimated_work.long_running
        ? `长耗时提醒：将运行 ${count} 组参数（常规提醒线 ${preview.estimated_work.warning_threshold} 组），并冻结 ${preview.prepared.universe.count} 只股票。\n\n这可能持续数小时并占用大量 CPU/内存，但可切换页面后继续查看进度。确认启动？`
        : `将运行 ${count} 组参数，冻结 ${preview.prepared.universe.count} 只股票。是否继续？`,
    )
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
    if (!task || !window.confirm('取消当前研究回测？已完成的运行记录会保留。')) return
    setBusy('cancel')
    try {
      setTask(await api.backtestCancel(task.task_id))
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy('')
    }
  }

  const handleActivateProfile = async () => {
    if (!task) return
    const confirmed = window.confirm(
      '确认把这组回测参数用于后续今日 A 池扫描？\n\n它仍是探索性候选，不代表收益承诺；B 池、资金、基本面和市场环境门禁不会改变。',
    )
    if (!confirmed) return
    setBusy('run')
    setError('')
    setProfileFeedback('')
    try {
      const next = await api.activateBacktestProfile(task.task_id)
      setProfileState(next)
      setTask(await api.backtestStatus(task.task_id))
      setProfileFeedback('已启用。下一次今日扫描会冻结并使用这组 A 池技术参数。')
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy('')
    }
  }

  const handleCopyEntry = async () => {
    if (!task || !profileState) return
    setBusy('run'); setError(''); setProfileFeedback('')
    try {
      const next = await api.copyBacktestEntry(task.task_id, profileState.active.config_hash)
      setProfileState(next)
      setProfileFeedback('已复制入场条件，下一次扫描生效。新配置待验证，不继承原回测收益。')
      setTask(await api.backtestStatus(task.task_id))
    } catch (reason) { setError(errorMessage(reason)) }
    finally { setBusy('') }
  }

  const handleClassificationChange = async (next: ClassificationKey) => {
    if (next === classification) return
    const previous = classification
    setClassification(next)
    setGroups([])
    setGroupFilter('')
    setPreview(null)
    setUniverseBusy(true)
    setError('')
    try {
      const nextUniverse = await api.backtestUniverse(next)
      if (nextUniverse.classification !== next) {
        throw new Error('分类接口返回了不一致的分类结果，请刷新后重试')
      }
      setUniverse(nextUniverse)
    } catch (reason) {
      setClassification(previous)
      setError(errorMessage(reason))
    } finally {
      setUniverseBusy(false)
    }
  }

  const visibleGroups = (universe?.groups || universe?.industries || [])
    .filter((item) => item.name.includes(groupFilter))
    .slice(0, 100)
  const classificationOptions = universe?.classifications || [{
    key: 'industry' as const,
    title: '细分行业',
    group_label: '行业',
    description: universe?.classification_note || '当前行业分类',
    pit_status: 'CURRENT_SNAPSHOT_ONLY' as const,
    group_count: universe?.industries?.length || 0,
  }]
  const selectedClassification = classificationOptions.find((item) => item.key === classification)
  const groupLabel = universe?.group_label || '板块'
  const activeTask = task && ACTIVE_STATUSES.includes(task.status)

  if (busy === 'load') return (
    <div className="research-workspace research-initial-state" role="status" aria-busy="true">
      <span className="guide-eyebrow">专业回测</span>
      <h1>正在准备研究工作台</h1>
      <p>读取参数配置、本地股票池与最近一次研究结果。</p>
      <div className="research-loading-lines" aria-hidden="true"><i /><i /><i /></div>
    </div>
  )
  if (!catalog || !universe) return (
    <div className="research-workspace research-initial-state" role="alert">
      <span className="guide-eyebrow">专业回测</span>
      <h1>暂时无法载入工作台</h1>
      <p>{error || '研究接口暂时不可用，请确认本地服务已启动。'}</p>
      <button className="btn" type="button" onClick={() => window.location.reload()}>重新载入</button>
    </div>
  )

  return (
    <div className="backtest-shell research-workspace fade-up">
      {checkDialog && (
        <ParameterCheckDialog
          result={checkDialog}
          onClose={() => setCheckDialog(null)}
          onViewPreview={() => {
            setCheckDialog(null)
            window.requestAnimationFrame(() => {
              if (typeof previewRef.current?.scrollIntoView === 'function') {
                previewRef.current.scrollIntoView({ block: 'nearest' })
              }
            })
          }}
        />
      )}
      <section className="backtest-intro">
        <div>
          <span className="guide-eyebrow">研究工作台 / 横盘吸筹突破</span>
          <h1>多参数研究回测</h1>
          <p>定义股票范围与交易规则，比较样本外表现、风险和成本。</p>
        </div>
        <div className="research-boundary">
          <b>探索性研究</b>
          <span>结果不是荐股或买入指令</span>
          <span>不连接券商，不生成订单</span>
        </div>
      </section>

      {error && <div className="guide-feedback error" role="alert"><b>无法继续</b><span>{error}</span></div>}
      {profileFeedback && <div className="guide-feedback success" role="status"><b>参数档案已更新</b><span>{profileFeedback}</span></div>}

      {(profileState || task) && <div className={`research-context-strip ${task?.status === 'done' ? 'is-complete' : ''}`}>
      {profileState && (
        <section className="active-profile-banner" aria-label="当前今日选股参数" title={`参数配置：${profileState.active.config_hash}${profileState.active.source.task_id ? ` · 来源任务：${profileState.active.source.task_id}` : ''}`}>
          <div>
            <div className="research-summary-line"><span className="guide-eyebrow">今日选股</span>
            <h2>{profileState.active.is_default ? '系统默认参数' : profileState.active.source.kind === 'MANUAL_RESEARCH' ? '用户手工研究参数' : profileState.active.source.kind === 'BACKTEST_ENTRY_COPY' ? '复制的入场条件 · 待验证' : '回测验证后人工启用参数'}</h2></div>
            <p>
              横盘 {String(profileState.active.entry.box_min_days)}–{String(profileState.active.entry.box_max_days)} 日 ·
              突破量比 ≥ {String(profileState.active.entry.breakout_vol_ratio)}
            </p>
          </div>
          <span className={`pill ${profileState.active.is_default ? '' : 'ok'}`}>
            {profileState.active.is_default
              ? '内置默认'
              : profileState.active.source.kind === 'BACKTEST_ENTRY_COPY'
                ? '来源 回测入场条件（待验证）'
              : profileState.active.source.kind === 'MANUAL_RESEARCH'
                ? '来源 手工输入（未回测验证）'
                : '来源 回测档案'}
          </span>
        </section>
      )}

      {task && (
        <section className={`task-status ${task.status}`} aria-live="polite" title={task.status === 'done' ? `${task.task_id} · ${task.message}` : undefined}>
          <div className="task-status-head">
            <div>
              <div className={task.status === 'done' ? 'research-summary-line' : undefined}><span className="guide-eyebrow">最近研究任务</span>
              <h2>{task.status === 'done' ? '回测已完成' : task.status === 'error' ? '回测失败' : task.status === 'cancelled' ? '回测已取消' : task.status === 'interrupted' ? '回测已中断' : task.message || '回测运行中'}</h2></div>
              {task.status === 'done' ? <p>更新于 {task.updated_at.slice(0, 16).replace('T', ' ')}</p> : <><p>{task.message}</p><span className="research-task-id mono">{task.task_id}</span></>}
            </div>
            <div className="research-task-actions">
              {activeTask && <span className="research-task-progress num">{task.progress}<small>%</small></span>}
              {activeTask && <button className="btn danger" type="button" onClick={handleCancel} disabled={busy === 'cancel'}>取消任务</button>}
            </div>
          </div>
          {activeTask && <div className="research-progress-track" role="progressbar" aria-label="研究任务进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={task.progress}><span style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} /></div>}
          {activeTask && <ol className="stage-strip">
            {PHASES.map(([phase, label], index) => {
              const currentIndex = PHASES.findIndex(([item]) => item === task.phase)
              const done = task.status === 'done' || currentIndex > index
              const current = currentIndex === index && activeTask
              return <li key={phase} className={done ? 'done' : current ? 'current' : ''}><span>{done ? '✓' : index + 1}</span>{label}</li>
            })}
          </ol>}
        </section>
      )}
      </div>}

      <details className="research-configuration" open={!task?.result}>
        <summary className="research-configuration-summary">
          <span><b>研究配置</b><small>股票池与数据 · 入场筛选 · 退出规则</small></span>
          <span className="research-configuration-hint">{task?.result ? '调整配置，开始新的研究' : '固定值、范围步进或离散值'}</span>
        </summary>
      <div className="backtest-layout">
        <aside className="backtest-config">
          <section aria-label="股票池与数据">
            <div className="config-heading"><h2><span className="research-section-index">01</span> 股票池与数据</h2><span>{groups.length ? `${groups.length} 个${groupLabel}` : '全市场'}</span></div>
            <p className="config-note">选择分类标准后再勾选细分方向。运行前会冻结代码与哈希。</p>
            <div className="field classification-field">
              <label htmlFor="universe-classification">分类标准</label>
              <select
                id="universe-classification"
                className="input"
                value={classification}
                disabled={universeBusy || Boolean(activeTask)}
                onChange={(event) => void handleClassificationChange(event.target.value as ClassificationKey)}
              >
                {classificationOptions.map((item) => (
                  <option key={item.key} value={item.key}>{item.title}（{item.group_count} 组）</option>
                ))}
              </select>
              <small>{selectedClassification?.description || universe.classification_note}</small>
            </div>
            <input
              className="input"
              value={groupFilter}
              onChange={(event) => setGroupFilter(event.target.value)}
              placeholder={`筛选${groupLabel}名称`}
              aria-label={`筛选${groupLabel}名称`}
            />
            <div className="industry-actions">
              <button className="btn btn-sm" type="button" onClick={() => mutate(() => setGroups(visibleGroups.map((item) => item.name)))}>勾选当前结果</button>
              <button className="btn btn-sm" type="button" onClick={() => mutate(() => setGroups([]))}>清空</button>
            </div>
            <div className="industry-picker" role="group" aria-label={`回测${groupLabel}`}>
              {universeBusy ? <div className="empty" role="status">正在读取新的分类...</div> : visibleGroups.length === 0 ? <p className="research-filter-empty">没有匹配的{groupLabel}，请调整筛选文字。</p> : visibleGroups.map((item) => (
                <label key={item.name}>
                  <input
                    type="checkbox"
                    checked={groups.includes(item.name)}
                    onChange={() => mutate(() => setGroups((current) => current.includes(item.name) ? current.filter((value) => value !== item.name) : [...current, item.name]))}
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
          {catalog.conditions.length > 0 && <details className="condition-section research-disclosure">
            <summary>研究条件扩展 <span>预留</span></summary>
            {(catalog.conditions || []).map((condition) => (
              <label className="condition-row" key={condition.id}>
                <input type="checkbox" checked={Boolean(conditionFlags[condition.id])} disabled={!condition.production_ready} onChange={(event) => mutate(() => setConditionFlags((current) => ({ ...current, [condition.id]: event.target.checked })))} />
                <span><b>{condition.title}</b><small>{condition.status}。数据 {condition.dataset?.available ? `${condition.dataset.rows} 行` : '未就绪'}。</small></span>
              </label>
            ))}
          </details>}
        </aside>

        <div className="backtest-workspace">
          <section className="card research-parameter-section" aria-label="入场筛选参数">
            <div className="h-sec"><h2><span className="research-section-index">02</span> 入场筛选</h2><span className="research-section-caption">什么条件下入选</span></div>
            <p className="config-note">设置横盘结构与突破条件。信号在收盘确认，按后续可交易日开盘模拟入场。</p>
            <div className="parameter-grid">
              {catalog.parameters.filter((item) => PRIMARY_ENTRY_KEYS.has(item.key)).map((definition) => (
                <ParameterEditor key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
              ))}
            </div>
            <details className="advanced-parameters research-disclosure">
              <summary>更多入场条件 <span>振幅、涨幅与结构约束</span></summary>
              <div className="parameter-grid">
                {catalog.parameters.filter((item) => !PRIMARY_ENTRY_KEYS.has(item.key) && !EXIT_KEYS.has(item.key)).map((definition) => (
                  <ParameterEditor key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
                ))}
              </div>
            </details>
          </section>

          <section className="card research-parameter-section research-exit-section" aria-label="退出规则参数">
            <div className="h-sec"><h2><span className="research-section-index">03</span> 退出规则</h2><span className="research-section-caption">入场后如何离场</span></div>
            <p className="config-note">止损、止盈与持有期限只影响回测离场，不改变股票入选条件。比例以百分比填写。</p>
            <div className="parameter-grid research-exit-primary">
              {catalog.parameters.filter((item) => EXIT_KEYS.has(item.key) && !ADVANCED_EXIT_KEYS.has(item.key)).map((definition) => (
                <ParameterEditor percentage={PERCENT_PARAMETER_KEYS.has(definition.key)} key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
              ))}
            </div>
            <details className="advanced-parameters research-disclosure" aria-label="高级退出规则">
              <summary>高级退出规则 <span>标杆量、出货窗口与强势重置</span></summary>
              <div className="parameter-grid">
                {catalog.parameters.filter((item) => ADVANCED_EXIT_KEYS.has(item.key)).map((definition) => (
                  <ParameterEditor key={definition.key} definition={definition} spec={parameters[definition.key]} onChange={(next) => mutate(() => setParameters((current) => ({ ...current, [definition.key]: next })))} />
                ))}
              </div>
            </details>
          </section>

          <section className="run-console section-gap">
            <div>
              <span className="guide-eyebrow">检查与运行 · 最多 {catalog.max_combinations.toLocaleString()} 组</span>
              <h2>{preview ? '回测输入已冻结预览' : '尚未生成运行预览'}</h2>
              <p>{preview ? preview.estimated_work.note : '调整参数或股票池后必须重新预览，防止误跑超大参数空间。'}</p>
            </div>
            <div className="run-actions">
              <button className="btn" type="button" onClick={handlePreview} disabled={busy === 'preview' || Boolean(activeTask)}>{busy === 'preview' ? '检查中...' : '检查参数空间'}</button>
              <button className="btn primary" type="button" onClick={handleRun} disabled={!preview?.can_run || busy === 'run' || Boolean(activeTask)}>{busy === 'run' ? '正在启动...' : '启动研究回测'}</button>
            </div>
          </section>

          {preview && (
            <section ref={previewRef} className="preview-grid" aria-label="回测预览">
              <div><span>有效组合</span><b>{preview.prepared.parameter_space.count}</b></div>
              <div><span>冻结股票</span><b>{preview.prepared.universe.count}</b></div>
              <div><span>动态预热</span><b>{preview.prepared.parameter_space.horizon} 日</b></div>
              <div><span>研究窗口</span><b>{formatDate(preview.prepared.windows.is[0])} 至 {formatDate(preview.prepared.windows.oos[1])}</b></div>
              <p>{preview.prepared.universe.classification_title}：{preview.prepared.universe.groups.length ? preview.prepared.universe.groups.join('、') : '全市场'}。{preview.prepared.universe.classification_note}</p>
              {preview.prepared.universe.sampling && <p>固定种子分层样本（不是按代码取前 N 只）：{Object.entries(preview.prepared.universe.sampling.sample_exchanges).map(([key, count]) => `${key === 'SH' ? '沪市' : '深市'} ${count} 只`).join('、')}，所选范围共 {preview.prepared.universe.sampling.population_count} 只。</p>}
              {preview.prepared.data_scope && <p role="status">数据范围检查：{preview.can_run ? '通过本次范围检查，运行时继续验证 PIT 版本' : '未通过'}。含预热从 {formatDate(preview.prepared.data_scope.warmup_start)} 起，共检查 {preview.prepared.data_scope.rows_checked} 条。{preview.prepared.data_scope.issues.join('；')}{preview.prepared.data_scope.note}</p>}
              {preview.estimated_work.long_running && (
                <p className="long-run-warning" role="alert">
                  长耗时任务：已超过 {preview.estimated_work.warning_threshold} 组常规提醒线。启动时会再次确认；可离开页面，后台进度会保留。
                </p>
              )}
            </section>
          )}

        </div>
      </div>
      </details>

          <ReplayCorrectionPanel />
          {task?.result && (
            <BacktestResultView
              result={task.result}
              reportIdentity={{ taskId: task.task_id, codeVersion: task.code_version }}
              activation={task.profile_activation}
              activeProfile={profileState}
              activating={busy === 'run'}
              onActivate={handleActivateProfile}
              entryCopy={task.entry_copy}
              onCopyEntry={handleCopyEntry}
            />
          )}
          {!task?.result && !activeTask && (
            <section className="research-empty" aria-label="尚无回测报告">
              <span className="guide-eyebrow">研究报告</span>
              <strong>{task?.status === 'error' ? '本次研究未生成完整报告' : '等待一次研究回测'}</strong>
              <p>{task?.status === 'error' ? '请查看上方失败原因，核对配置后再试。未完成的任务不会生成推测结果。' : '完成配置后，先检查参数空间，再启动回测。这里将展示样本外收益、最大回撤、成本压力与逐笔账户复盘。'}</p>
              <div className="research-empty-steps"><span>01 定义范围</span><span>02 检查配置</span><span>03 阅读证据</span></div>
            </section>
          )}
    </div>
  )
}
