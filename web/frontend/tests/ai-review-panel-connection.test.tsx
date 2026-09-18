import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, type AIReview, type AIReviewGeneration, type ExternalAIInsight } from '../src/api/client'
import AIReviewPanel from '../src/components/AIReviewPanel'

const providers = [
  { id: 'deepseek', label: 'DeepSeek', configured: false, model: 'deepseek-chat' },
  { id: 'openai', label: 'OpenAI', configured: true, model: 'configured-openai-model' },
  { id: 'ollama', label: 'Ollama 本地模型', configured: false, model: '' },
]
const configured: AIReviewGeneration = { available: true, status: 'configured', provider: 'openai', model: 'configured-openai-model', message: 'OpenAI 配置已读取', providers }
const unconfigured: AIReviewGeneration = { available: false, status: 'not_configured', provider: 'deepseek', model: 'deepseek-chat', message: '未配置外部模型；本地证据评测已可独立使用', providers: providers.map(row => ({ ...row, configured: false })) }
const review = (code = '000001.SZ', generation = configured, changes: Partial<AIReview> = {}): AIReview => ({
  ts_code: code, name: code, industry: '银行', verdict: 'SUPPORTS_MONITORING', verdict_label: '证据支持继续观察',
  as_of: '20260911', signal_date: '20260911', evidence: [{ code: 'LOCAL', label: `${code} 本地证据`, value: '有记录' }], risks: [],
  data: { close: 10, pe: null, pb: null, roe: null, box_high: 9.8, box_days: 80, breakout_vol_ratio: 1.8 },
  external_ai: null, generation,
  boundary: { read_only: true, changes_scan_or_signal: false, triggers_order: false, message: '只读，不改变选股。' }, ...changes,
})
const generated = (code = '000001.SZ', provider = 'openai'): ExternalAIInsight => ({ ts_code: code, signal_date: '20260911', provider, model: 'actual-response-model', ai_text: `${code} 本次模型解读` })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const ready = () => screen.findByText('已配置，尚未验证调用')

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('model connection state and request isolation', () => {
  it('uses the server-selected OpenAI provider without an automatic generation call', async () => {
    vi.spyOn(api, 'aiReview').mockResolvedValue(review())
    const post = vi.spyOn(api, 'aiReviewGenerate')
    render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    expect(screen.getByRole('combobox', { name: '解读提供方' })).toHaveValue('openai')
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['openai'])
    expect(screen.getByText(/配置状态不代表连接或模型调用已通过验证/)).toBeTruthy()
    expect(screen.queryByText('模型调用成功')).toBeNull()
    expect(post).not.toHaveBeenCalled()
  })

  it('sends the explicitly selected configured provider with 75-second timeout and an abort signal', async () => {
    const both = { ...configured, provider: 'deepseek', model: 'deepseek-chat', providers: providers.map(row => ({ ...row, configured: row.id !== 'ollama' })) }
    vi.spyOn(api, 'aiReview').mockResolvedValue(review('000001.SZ', both))
    const post = vi.spyOn(api, 'aiReviewGenerate').mockResolvedValue({ review: review('000001.SZ', both), generated: generated() })
    render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['deepseek', 'openai'])
    fireEvent.change(screen.getByRole('combobox', { name: '解读提供方' }), { target: { value: 'openai' } })
    fireEvent.click(screen.getByRole('button', { name: '生成 AI 文字解读' }))
    expect(await screen.findByText('模型调用成功')).toBeTruthy()
    expect(post).toHaveBeenCalledWith('000001.SZ', 'openai', expect.objectContaining({ timeoutMs: 75000, signal: expect.any(AbortSignal) }))
    expect(screen.getByText(/本次调用成功 · openai · actual-response-model/)).toBeTruthy()
    expect(screen.getByText('本次模型文字解读 · openai · actual-response-model')).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox', { name: '解读提供方' }), { target: { value: 'deepseek' } })
    expect(screen.queryByText('模型调用成功')).toBeNull()
    expect(screen.getByText('本次模型文字解读 · openai · actual-response-model')).toBeTruthy()
    expect(screen.queryByText(/已保存的模型文字解读/)).toBeNull()
  })

  it('refreshes configuration with GET only and explains local setup without accepting credentials', async () => {
    const get = vi.spyOn(api, 'aiReview').mockResolvedValueOnce(review('000001.SZ', unconfigured)).mockResolvedValueOnce(review())
    const post = vi.spyOn(api, 'aiReviewGenerate')
    render(<AIReviewPanel tsCode="000001.SZ" />)
    expect(await screen.findByText('模型未配置')).toBeTruthy()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByRole('button', { name: 'AI 文字解读未配置' })).toBeDisabled()
    fireEvent.click(screen.getByText('连接模型'))
    expect(screen.getByText('DEEPSEEK_API_KEY')).toBeVisible()
    expect(screen.getByText('OPENAI_BASE_URL')).toBeVisible()
    expect(screen.getByText('OLLAMA_MODEL')).toBeVisible()
    expect(document.querySelector('input')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '刷新模型状态' }))
    await ready()
    expect(get).toHaveBeenCalledTimes(2)
    expect(post).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '生成 AI 文字解读' })).toBeEnabled()
  })

  it('updates the status message when a configured provider is selected instead of an unsupported default', async () => {
    const unsupported: AIReviewGeneration = { ...configured, provider: 'unknown', available: false, status: 'unsupported', model: '', message: '默认提供方不支持', providers }
    vi.spyOn(api, 'aiReview').mockResolvedValue(review('000001.SZ', unsupported))
    vi.spyOn(api, 'aiReviewGenerate')
    render(<AIReviewPanel tsCode="000001.SZ" />)
    expect(await screen.findByText('当前模型提供方不受支持')).toBeTruthy()
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['unknown', 'openai'])
    fireEvent.change(screen.getByRole('combobox', { name: '解读提供方' }), { target: { value: 'openai' } })
    expect(screen.getByText('已配置，尚未验证调用')).toBeTruthy()
    expect(screen.queryByText('默认提供方不支持')).toBeNull()
    expect(screen.getByText(/OpenAI 已配置，可按需生成文字解读/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '生成 AI 文字解读' })).toBeEnabled()
  })

  it('distinguishes a local GET failure from an external generation failure and offers a GET retry', async () => {
    const get = vi.spyOn(api, 'aiReview').mockRejectedValueOnce(new Error('本地数据暂时不可读')).mockResolvedValueOnce(review())
    render(<AIReviewPanel tsCode="000001.SZ" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('本地证据评测加载失败')
    expect(screen.queryByText('AI 文字解读生成失败')).toBeNull()
    expect(screen.queryByText(/本地证据评测仍/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '刷新模型状态' }))
    await ready()
    expect(get).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['AI_AUTH_FAILED', '模型凭据验证失败'], ['AI_RATE_LIMIT', '模型请求达到限额'],
    ['AI_TIMEOUT', '模型响应超时'], ['AI_INVALID_RESPONSE', '模型未返回有效文本'],
  ])('keeps local evidence when generation returns %s', async (code, message) => {
    vi.spyOn(api, 'aiReview').mockResolvedValue(review())
    vi.spyOn(api, 'aiReviewGenerate').mockRejectedValue(new ApiError({ code, message, status: 502, retryable: code !== 'AI_AUTH_FAILED' }))
    render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    fireEvent.click(screen.getByRole('button', { name: '生成 AI 文字解读' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('AI 文字解读生成失败')
    expect(alert).toHaveTextContent(message)
    expect(screen.getByText('000001.SZ 本地证据')).toBeTruthy()
    expect(screen.queryByText('模型调用成功')).toBeNull()
  })

  it('does not keep old local evidence after switching to a stock whose GET fails', async () => {
    vi.spyOn(api, 'aiReview').mockResolvedValueOnce(review()).mockRejectedValueOnce(new Error('新股票暂无本地行情'))
    const { rerender } = render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    rerender(<AIReviewPanel tsCode="000002.SZ" />)
    expect(screen.queryByText('000001.SZ 本地证据')).toBeNull()
    expect(await screen.findByRole('alert')).toHaveTextContent('本地证据评测加载失败')
    expect(screen.queryByText('当前股票的本地证据评测仍可查看。')).toBeNull()
  })

  it('aborts and ignores an older local response after tsCode changes', async () => {
    const old = deferred<AIReview>()
    const get = vi.spyOn(api, 'aiReview').mockReturnValueOnce(old.promise).mockResolvedValueOnce(review('000002.SZ'))
    const { rerender } = render(<AIReviewPanel tsCode="000001.SZ" />)
    const signal = get.mock.calls[0][1]?.signal
    rerender(<AIReviewPanel tsCode="000002.SZ" />)
    expect(await screen.findByText('000002.SZ 本地证据')).toBeTruthy()
    expect(signal?.aborted).toBe(true)
    await act(async () => { old.resolve(review()); await old.promise })
    expect(screen.queryByText('000001.SZ 本地证据')).toBeNull()
  })

  it('aborts and ignores an older generation response after tsCode changes', async () => {
    const old = deferred<{ review: AIReview; generated: ExternalAIInsight }>()
    vi.spyOn(api, 'aiReview').mockResolvedValueOnce(review()).mockResolvedValueOnce(review('000002.SZ'))
    const post = vi.spyOn(api, 'aiReviewGenerate').mockReturnValueOnce(old.promise)
    const { rerender } = render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    fireEvent.click(screen.getByRole('button', { name: '生成 AI 文字解读' }))
    const signal = post.mock.calls[0][2]?.signal
    rerender(<AIReviewPanel tsCode="000002.SZ" />)
    expect(await screen.findByText('000002.SZ 本地证据')).toBeTruthy()
    expect(signal?.aborted).toBe(true)
    await act(async () => { old.resolve({ review: review(), generated: generated() }); await old.promise })
    expect(screen.queryByText('000001.SZ 本次模型解读')).toBeNull()
    expect(screen.queryByText('模型调用成功')).toBeNull()
    expect(screen.getByRole('button', { name: '生成 AI 文字解读' })).toBeEnabled()
  })

  it('labels cached narrative with its saved provider and time without claiming a fresh successful call', async () => {
    const cached = { ...generated('000001.SZ', 'deepseek'), created_at: '2026-09-11T17:00:00+08:00', ai_text: '此前保存的解读', cached: true }
    vi.spyOn(api, 'aiReview').mockResolvedValue(review('000001.SZ', configured, { external_ai: cached }))
    const post = vi.spyOn(api, 'aiReviewGenerate')
    render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    expect(screen.getByText('已保存的模型文字解读 · deepseek · actual-response-model')).toBeTruthy()
    expect(screen.getByText(/生成时间 2026-09-11T17:00:00/)).toBeTruthy()
    expect(screen.getByText('此前保存的解读')).toBeTruthy()
    expect(screen.queryByText('模型调用成功')).toBeNull()
    expect(post).not.toHaveBeenCalled()
  })

  it('rejects a response whose stock identity differs from the requested stock', async () => {
    vi.spyOn(api, 'aiReview').mockResolvedValue(review())
    vi.spyOn(api, 'aiReviewGenerate').mockResolvedValue({ review: review(), generated: generated('000002.SZ') })
    render(<AIReviewPanel tsCode="000001.SZ" />)
    await ready()
    fireEvent.click(screen.getByRole('button', { name: '生成 AI 文字解读' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('不属于当前股票')
    expect(screen.queryByText('000002.SZ 本次模型解读')).toBeNull()
  })

  it('does not force a DeepSeek provider when the API caller leaves provider unset', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetch)
    await api.aiReviewGenerate('000001.SZ')
    expect(fetch).toHaveBeenCalledOnce()
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({})
  })
})
