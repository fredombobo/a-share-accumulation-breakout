import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type AIReview, type AIReviewProvider } from '../api/client'

const providerLabels: Record<string, string> = { deepseek: 'DeepSeek', openai: 'OpenAI', ollama: 'Ollama 本地模型' }
const normalizeCode = (code: string) => code.trim().toUpperCase()

function failureText(error: unknown, kind: 'local' | 'generation'): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.name === 'AbortError') return kind === 'generation'
    ? '等待模型响应超时，请稍后重试。' : '本地证据请求超时，请刷新重试。'
  return error instanceof Error ? error.message : String(error)
}
function modelOptions(review: AIReview): AIReviewProvider[] {
  const generation = review.generation
  if (!generation) return []
  const options = (generation.providers || []).filter(item => item.configured || item.id === generation.provider)
  if (generation.provider && !options.some(item => item.id === generation.provider)) {
    options.unshift({ id: generation.provider, label: providerLabels[generation.provider] || generation.provider,
      configured: generation.available && generation.status !== 'unsupported', model: generation.model || '' })
  }
  return options
}

export default function AIReviewPanel({ tsCode }: { tsCode: string }) {
  const code = normalizeCode(tsCode)
  const currentCode = useRef(code)
  currentCode.current = code
  const localEpoch = useRef(0)
  const generationEpoch = useRef(0)
  const localController = useRef<AbortController | null>(null)
  const generationController = useRef<AbortController | null>(null)
  const [storedReview, setReview] = useState<AIReview | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [localError, setLocalError] = useState('')
  const [generationError, setGenerationError] = useState('')
  const [selectedProvider, setSelectedProvider] = useState('')
  const [success, setSuccess] = useState<{ code: string; provider: string; model: string } | null>(null)
  const [narrativeSource, setNarrativeSource] = useState<'current' | 'saved'>('saved')
  // Hide stale stock content even before a changed prop's effect has run.
  const review = storedReview && normalizeCode(storedReview.ts_code) === code ? storedReview : null

  const loadReview = async (requestCode: string, resetProvider = false) => {
    const epoch = ++localEpoch.current
    ++generationEpoch.current
    localController.current?.abort()
    generationController.current?.abort()
    const controller = new AbortController()
    localController.current = controller
    setLoading(true)
    setGenerating(false)
    setLocalError('')
    setGenerationError('')
    setSuccess(null)
    setNarrativeSource('saved')
    setReview(null)
    if (resetProvider) setSelectedProvider('')
    try {
      const value = await api.aiReview(requestCode, { signal: controller.signal })
      if (controller.signal.aborted || epoch !== localEpoch.current || currentCode.current !== requestCode) return
      if (normalizeCode(value.ts_code) !== requestCode) throw new Error('返回的本地证据不属于当前股票，请刷新重试。')
      setReview(value)
      const options = modelOptions(value)
      setSelectedProvider(previous => !resetProvider && options.some(item => item.id === previous)
        ? previous : value.generation?.provider || options.find(item => item.configured)?.id || '')
      if (window.location.hash === '#ai-review') {
        window.requestAnimationFrame(() => {
          if (epoch === localEpoch.current && currentCode.current === requestCode) document.getElementById('ai-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        })
      }
    } catch (reason) {
      if (!controller.signal.aborted && epoch === localEpoch.current && currentCode.current === requestCode) setLocalError(failureText(reason, 'local'))
    } finally {
      if (!controller.signal.aborted && epoch === localEpoch.current && currentCode.current === requestCode) setLoading(false)
    }
  }

  useEffect(() => {
    void loadReview(code, true)
    return () => {
      ++localEpoch.current
      ++generationEpoch.current
      localController.current?.abort()
      generationController.current?.abort()
    }
  }, [code])

  const options = review ? modelOptions(review) : []
  const selected = options.find(item => item.id === selectedProvider)
  const unsupported = Boolean(review?.generation?.status === 'unsupported' && selectedProvider === review.generation.provider)
  const configured = Boolean(selected?.configured && !unsupported)
  const currentSuccess = success?.code === code && success.provider === selectedProvider ? success : null
  const statusText = unsupported ? '当前模型提供方不受支持' : !configured ? '模型未配置'
    : currentSuccess ? '模型调用成功' : '已配置，尚未验证调用'
  const configurationMessage = review?.generation && selectedProvider !== review.generation.provider
    ? configured ? `${selected?.label || selectedProvider} 已配置，可按需生成文字解读。` : '所选模型尚未配置。'
    : review?.generation?.message || '本地证据评测已可独立使用'

  const generate = async () => {
    if (!review || !configured || !selectedProvider || generating || loading) return
    const requestCode = code
    const provider = selectedProvider
    const epoch = ++generationEpoch.current
    generationController.current?.abort()
    const controller = new AbortController()
    generationController.current = controller
    setGenerating(true)
    setGenerationError('')
    setSuccess(null)
    try {
      const response = await api.aiReviewGenerate(requestCode, provider, { signal: controller.signal, timeoutMs: 75_000 })
      if (controller.signal.aborted || epoch !== generationEpoch.current || currentCode.current !== requestCode) return
      if (normalizeCode(response.review.ts_code) !== requestCode || normalizeCode(response.generated.ts_code) !== requestCode) {
        throw new Error('返回的模型解读不属于当前股票，已忽略该结果。')
      }
      if (response.generated.provider !== provider || !response.generated.ai_text?.trim()) {
        throw new Error('模型返回的提供方或解读内容无效，请重试。')
      }
      setReview({ ...response.review, external_ai: response.generated })
      setNarrativeSource('current')
      setSuccess({ code: requestCode, provider: response.generated.provider, model: response.generated.model || '' })
    } catch (reason) {
      if (!controller.signal.aborted && epoch === generationEpoch.current && currentCode.current === requestCode) setGenerationError(failureText(reason, 'generation'))
    } finally {
      if (!controller.signal.aborted && epoch === generationEpoch.current && currentCode.current === requestCode) setGenerating(false)
    }
  }

  const verdictClass = review?.verdict === 'SUPPORTS_MONITORING' ? 'ok' : review?.verdict === 'MIXED_EVIDENCE' ? 'warn' : 'muted'
  const narrative = review?.external_ai && normalizeCode(review.external_ai.ts_code) === code ? review.external_ai : null

  return (
    <section id="ai-review" className="card ai-review section-gap" aria-live="polite">
      <div className="h-sec">
        <div><span className="guide-eyebrow">只读辅助解释</span><h2>AI 证据评测</h2></div>
        {review && <span className={`pill ${verdictClass}`}>{review.verdict_label}</span>}
      </div>
      <p className="ai-boundary">本地规则先做确定性评测。AI 只解释已有证据，不改变候选、分数或任何交易状态。</p>
      {loading && <div className="loading">正在核对本地证据与模型配置...</div>}
      {localError && <div className="guide-feedback error" role="alert"><b>本地证据评测加载失败</b><span>{localError}</span></div>}
      {generationError && <div className="guide-feedback error" role="alert"><b>AI 文字解读生成失败</b><span>{generationError}</span>{review && <span>当前股票的本地证据评测仍可查看。</span>}</div>}
      {review && <>
        <div className="evidence-grid">
          <div><h3>支持证据</h3>{review.evidence.length ? review.evidence.map(item => <article key={`${item.code}-${item.as_of || ''}`}><b>{item.label}</b><span>{item.value || '已识别'}</span><small>{item.as_of || review.as_of || '时点未知'}</small></article>) : <div className="evidence-empty">没有足够支持证据</div>}</div>
          <div><h3>风险与缺口</h3>{review.risks.length ? review.risks.map(item => <article className="risk" key={`${item.code}-${item.as_of || ''}`}><b>{item.label}</b><span>{item.value || '需要补充数据'}</span><small>{item.as_of || review.as_of || '时点未知'}</small></article>) : <div className="evidence-empty">未发现已编码风险，但不代表没有风险</div>}</div>
        </div>
        <div className="ai-model-status" role="status">
          <div><strong>{statusText}</strong><span>{selected?.label || selectedProvider || '外部模型'}{selected?.model ? ` · ${selected.model}` : ''}</span></div>
          {currentSuccess ? <p>本次调用成功 · {currentSuccess.provider} · {currentSuccess.model || '模型名称未记录'}。本次生成的解读仅在当前页面展示。</p> : <p>{configurationMessage}{configured ? ' 配置状态不代表连接或模型调用已通过验证。' : ''}</p>}
        </div>
        <div className="ai-review-actions">
          <span>证据时点 {review.as_of || 'n/a'} · 信号时点 {review.signal_date || 'n/a'}</span>
          <div className="ai-model-controls">
            {options.some(item => item.configured) && <label>解读提供方<select aria-label="解读提供方" value={selectedProvider} disabled={loading || generating} onChange={event => { setSelectedProvider(event.target.value); setGenerationError(''); setSuccess(null) }}>{options.map(item => <option key={item.id} value={item.id}>{item.label}{item.configured ? '' : '（未配置）'}</option>)}</select></label>}
            <button className="btn" type="button" onClick={() => void generate()} disabled={generating || loading || !configured} title={configured ? '仅在点击后调用所选模型解释已有证据' : statusText}>{generating ? '正在生成...' : unsupported ? '当前模型不可用' : !configured ? 'AI 文字解读未配置' : narrative ? '重新生成 AI 文字解读' : '生成 AI 文字解读'}</button>
          </div>
        </div>
        {!configured && <details className="ai-connect-help"><summary>连接模型</summary>
          <p>在运行此服务的本机项目根目录 <code>.env</code> 中配置所选提供方，保存后点击“刷新模型状态”。此页面不接收或保存 API Key；刷新只核对配置，不调用外部模型。</p>
          <dl>
            <div><dt>DeepSeek</dt><dd><code>DEEPSEEK_API_KEY</code>；可选 <code>DEEPSEEK_MODEL</code>、<code>DEEPSEEK_BASE_URL</code>。</dd></div>
            <div><dt>OpenAI / 兼容服务</dt><dd><code>OPENAI_API_KEY</code>；可选 <code>OPENAI_MODEL</code>、<code>OPENAI_BASE_URL</code>。</dd></div>
            <div><dt>Ollama 本地模型</dt><dd>明确设置已安装的 <code>OLLAMA_MODEL</code>；可选 <code>OLLAMA_BASE_URL</code>，默认 <code>http://localhost:11434/v1</code>。</dd></div>
          </dl>
          <p>可用 <code>AI_PROVIDER=deepseek</code>、<code>openai</code> 或 <code>ollama</code> 指定默认提供方；不指定时由服务端选择已配置项。只有主动点击生成后，才会尝试连接。</p>
        </details>}
        {narrative?.ai_text && <details className="ai-narrative" open>
          <summary>{narrativeSource === 'current' ? '本次模型文字解读' : '已保存的模型文字解读'} · {narrative.provider}{narrative.model ? ` · ${narrative.model}` : ''}</summary>
          {narrativeSource === 'saved' ? <small className="ai-narrative-meta">生成时间 {narrative.created_at || '未记录'}；这是已有解读，不代表本次模型连接已验证。</small> : <small className="ai-narrative-meta">本次生成的解读仅在当前页面展示。</small>}
          <div>{narrative.ai_text}</div>
        </details>}
        <div className="note">{review.boundary.message}</div>
      </>}
      <div className="ai-status-refresh"><button className="btn" type="button" onClick={() => void loadReview(code)} disabled={loading || generating}>刷新模型状态</button><span>仅刷新本地证据和配置，不自动生成解读。</span></div>
    </section>
  )
}
