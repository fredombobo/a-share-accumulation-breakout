import type { LabCatalog, LabResearchStatus, LabStatusResp, LabTrustedReport } from '../../api/client'
import { FriendlyError, GuideSteps, ViewModeToggle } from '../guidance/BeginnerUi'

type Strategy = 'A' | 'B'

const stages = ['数据检查', '计算含费用结果', '用未参与调参的数据验证', '分时段与基线复验', '生成结论']

function stageIndex(task: LabStatusResp | null): number {
  if (!task || task.status === 'idle') return 0
  if (task.status === 'done') return 5
  const phase = String(task.phase || '').toUpperCase()
  if (phase.includes('CANDIDATE') || phase.includes('REPORT')) return 4
  if (phase.includes('WF') || phase.includes('BASELINE')) return 3
  if (phase.includes('OOS')) return 2
  if (phase.includes('IS') || phase.includes('NET')) return 1
  return Math.min(4, Math.max(0, Math.floor(Number(task.progress || 0) / 20)))
}

function verdictText(report: LabTrustedReport) {
  if (report.verdict === 'PASS') {
    return { title: '可以作为候选继续观察', tone: 'pass',
             explanation: '完整验证已通过，但不会自动进入 A 池或生成订单。' }
  }
  if (report.verdict === 'FAIL') {
    return { title: '当前不建议使用', tone: 'fail',
             explanation: '至少一项可信门禁未通过，请不要依据这组参数交易。' }
  }
  return { title: '证据不足，不能判断', tone: 'insufficient',
           explanation: '样本或验证窗口不足，当前结果只能用于了解流程。' }
}

export default function LabGuided({
  strategy,
  onStrategy,
  catalog,
  research,
  task,
  report,
  error,
  running,
  onRun,
  onCancel,
  onAdvanced,
}: {
  strategy: Strategy
  onStrategy: (strategy: Strategy) => void
  catalog: LabCatalog | null
  research: LabResearchStatus | null
  task: LabStatusResp | null
  report: LabTrustedReport | null
  error: unknown
  running: boolean
  onRun: () => void
  onCancel: () => void
  onAdvanced: () => void
}) {
  const plan = research?.plan
  const ready = Boolean(plan?.data_ready_for_edge_validation && plan.mode === 'full')
  const verdict = report ? verdictText(report) : null

  return (
    <div className="guided-page lab-guided">
      <div className="guide-page-head">
        <div>
          <span className="guide-eyebrow">小白模式</span>
          <h1>验证一个策略是否值得继续研究</h1>
          <p>只需选择方案并开始。系统会自动使用完整历史窗口、费用、样本外和分段复验。</p>
        </div>
        <ViewModeToggle mode="guided" onChange={onAdvanced} />
      </div>

      <section className="guide-card">
        <h2>第一步：选择要验证的方案</h2>
        <div className="guide-choice-grid">
          {(['A', 'B'] as Strategy[]).map((id) => {
            const doc = catalog?.strategies?.[id]
            return (
              <button key={id} type="button" className={`guide-choice ${strategy === id ? 'selected' : ''}`}
                aria-pressed={strategy === id} onClick={() => onStrategy(id)} disabled={running}>
                <strong>方案 {id} · {doc?.name || (id === 'A' ? '形态突破' : '趋势增强')}</strong>
                <span>{doc?.tagline || (id === 'A' ? '寻找整理后放量启动' : '观察趋势逐步增强')}</span>
                <small>{strategy === id ? '已选择' : '点击选择'}</small>
              </button>
            )
          })}
        </div>
      </section>

      <section className={`guide-readiness ${ready ? 'ready' : 'blocked'}`}>
        <div>
          <strong>{ready ? '数据已准备好，可以做完整验证' : '数据还不足，暂时不能给出可信结论'}</strong>
          <span>{plan ? `${plan.n_dates} 个交易日 · ${plan.earliest || '—'} 至 ${plan.latest || '—'}` : '正在检查本地数据…'}</span>
        </div>
        {!ready && <p>请先完成历史数据补齐；在此之前可使用下方示例了解报告。</p>}
      </section>

      {(running || task?.status === 'done') && (
        <section className="guide-card">
          <h2>{running ? '系统正在验证，你可以离开本页' : '验证流程已完成'}</h2>
          <GuideSteps labels={stages} current={stageIndex(task)} />
          {task?.task_id && <p className="guide-task-line">任务 {task.task_id} · {task.message || '正在准备'} · {task.progress || 0}%</p>}
        </section>
      )}

      {verdict && report && (
        <section id="lab-conclusion" className={`guide-verdict ${verdict.tone}`}>
          <span>可信研究结论</span>
          <h2>{verdict.title}</h2>
          <p>{verdict.explanation}</p>
          {(report.block_reasons || []).slice(0, 3).length > 0 && (
            <ul>{report.block_reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>
          )}
          <button type="button" className="btn" onClick={onAdvanced}>展开专业报告</button>
        </section>
      )}

      <FriendlyError error={error} />

      <div className="guide-primary-actions">
        {!running ? (
          <button type="button" className="btn primary guide-primary" disabled={!ready || !catalog} onClick={onRun}>
            开始可信验证方案 {strategy}
          </button>
        ) : (
          <button type="button" className="btn danger" onClick={onCancel}>取消验证</button>
        )}
        <span>推荐配置：600 只股票、54 组参数、自动完整验证窗口</span>
      </div>

      <details className="guide-tutorial">
        <summary>第一次使用：先看三种结论示例</summary>
        <div className="guide-example-grid">
          <article><strong>可以作为候选继续观察</strong><p>所有门禁通过，仍然不会自动下单。</p></article>
          <article><strong>当前不建议使用</strong><p>样本外、回撤或基线至少一项未通过。</p></article>
          <article><strong>证据不足</strong><p>数据太少，只能了解流程，不能判断策略好坏。</p></article>
        </div>
      </details>
    </div>
  )
}
