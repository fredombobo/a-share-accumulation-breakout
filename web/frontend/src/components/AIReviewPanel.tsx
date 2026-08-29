import { useEffect, useState } from 'react'
import { AIReview, ApiError, api } from '../api/client'

function failureText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : String(error)
}

export default function AIReviewPanel({ tsCode }: { tsCode: string }) {
  const [review, setReview] = useState<AIReview | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api.aiReview(tsCode)
      .then((value) => {
        if (!active) return
        setReview(value)
        if (window.location.hash === '#ai-review') {
          window.requestAnimationFrame(() => document.getElementById('ai-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
        }
      })
      .catch((reason) => active && setError(failureText(reason)))
      .finally(() => active && setLoading(false))
    return () => { active = false }
  }, [tsCode])

  const generate = async () => {
    setGenerating(true)
    setError('')
    try {
      const response = await api.aiReviewGenerate(tsCode)
      setReview({ ...response.review, external_ai: response.generated })
    } catch (reason) {
      setError(failureText(reason))
    } finally {
      setGenerating(false)
    }
  }

  const verdictClass = review?.verdict === 'SUPPORTS_MONITORING'
    ? 'ok'
    : review?.verdict === 'MIXED_EVIDENCE'
      ? 'warn'
      : 'muted'

  return (
    <section id="ai-review" className="card ai-review section-gap" aria-live="polite">
      <div className="h-sec">
        <div>
          <span className="guide-eyebrow">只读辅助解释</span>
          <h2>AI 证据评测</h2>
        </div>
        {review && <span className={`pill ${verdictClass}`}>{review.verdict_label}</span>}
      </div>
      <p className="ai-boundary">本地规则先做确定性评测。AI 只解释已有证据，不改变候选、分数或任何交易状态。</p>
      {loading && <div className="loading">正在核对本地行情、形态、资金与财务证据...</div>}
      {error && <div className="guide-feedback error" role="alert"><b>AI 文字解读暂不可用</b><span>{error}</span>{review && <span>本地证据评测仍然有效。</span>}</div>}
      {review && (
        <>
          <div className="evidence-grid">
            <div>
              <h3>支持证据</h3>
              {review.evidence.length ? review.evidence.map((item) => (
                <article key={`${item.code}-${item.as_of || ''}`}>
                  <b>{item.label}</b><span>{item.value || '已识别'}</span><small>{item.as_of || review.as_of || '时点未知'}</small>
                </article>
              )) : <div className="evidence-empty">没有足够支持证据</div>}
            </div>
            <div>
              <h3>风险与缺口</h3>
              {review.risks.length ? review.risks.map((item) => (
                <article className="risk" key={`${item.code}-${item.as_of || ''}`}>
                  <b>{item.label}</b><span>{item.value || '需要补充数据'}</span><small>{item.as_of || review.as_of || '时点未知'}</small>
                </article>
              )) : <div className="evidence-empty">未发现已编码风险，但不代表没有风险</div>}
            </div>
          </div>
          <div className="ai-review-actions">
            <span>证据时点 {review.as_of || 'n/a'} · 信号时点 {review.signal_date || 'n/a'}</span>
            <button className="btn" type="button" onClick={generate} disabled={generating}>
              {generating ? '正在生成...' : review.external_ai ? '重新生成 AI 文字解读' : '生成 AI 文字解读'}
            </button>
          </div>
          {review.external_ai?.ai_text && (
            <details className="ai-narrative" open>
              <summary>外部模型文字解读 · {review.external_ai.provider}</summary>
              <div>{review.external_ai.ai_text}</div>
            </details>
          )}
          <div className="note">{review.boundary.message}</div>
        </>
      )}
    </section>
  )
}
