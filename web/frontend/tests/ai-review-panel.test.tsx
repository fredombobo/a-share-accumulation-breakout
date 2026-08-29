import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, type AIReview } from '../src/api/client'
import AIReviewPanel from '../src/components/AIReviewPanel'

const review: AIReview = {
  ts_code: '000001.SZ', name: '测试股份', industry: '银行', verdict: 'SUPPORTS_MONITORING',
  verdict_label: '证据支持继续观察', as_of: '20260828', signal_date: '20260828',
  evidence: [{ code: 'BREAKOUT_VOLUME', label: '突破量比', value: '1.80 倍', as_of: '20260828' }],
  risks: [{ code: 'FINANCIAL_DATA_MISSING', label: '财务证据缺失' }],
  data: { close: 10, pe: null, pb: null, roe: null, box_high: 9.8, box_days: 80, breakout_vol_ratio: 1.8 },
  external_ai: null,
  generation: { available: false, provider: 'deepseek', message: '未配置外部模型；本地证据评测已可独立使用' },
  boundary: { read_only: true, changes_scan_or_signal: false, triggers_order: false, message: '只读，不改变选股。' },
}

afterEach(() => vi.restoreAllMocks())

describe('个股 AI 证据评测', () => {
  it('外部模型未配置时仍保留本地评测', async () => {
    vi.spyOn(api, 'aiReview').mockResolvedValue(review)
    const generate = vi.spyOn(api, 'aiReviewGenerate').mockRejectedValue(new ApiError({
      code: 'AI_PROVIDER_NOT_CONFIGURED', message: 'deepseek 未配置；本地证据评测仍可使用', status: 503, retryable: true,
    }))

    render(<AIReviewPanel tsCode="000001.SZ" />)
    expect(await screen.findByText('证据支持继续观察')).toBeVisible()
    expect(screen.getByText('突破量比')).toBeVisible()
    const button = screen.getByRole('button', { name: 'AI 文字解读未配置' })
    expect(button).toBeDisabled()
    expect(screen.getByText(/本地证据评测已可独立使用/)).toBeVisible()
    fireEvent.click(button)
    await waitFor(() => expect(generate).not.toHaveBeenCalled())
  })
})
