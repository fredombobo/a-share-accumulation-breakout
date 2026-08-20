import { useCallback, useState } from 'react'
import { ApiError } from '../../api/client'

export type ViewMode = 'guided' | 'advanced'

export function useViewMode(key: 'lab' | 'paper') {
  const storageKey = `ab.ui.mode.${key}.v1`
  const [mode, setModeState] = useState<ViewMode>(() => {
    const stored = window.localStorage.getItem(storageKey)
    return stored === 'advanced' ? 'advanced' : 'guided'
  })
  const setMode = useCallback((next: ViewMode) => {
    window.localStorage.setItem(storageKey, next)
    setModeState(next)
  }, [storageKey])
  return { mode, setMode }
}

export function ViewModeToggle({ mode, onChange }: {
  mode: ViewMode
  onChange: (mode: ViewMode) => void
}) {
  return (
    <div className="guide-mode-switch" aria-label="界面模式">
      <span>{mode === 'guided' ? '小白模式' : '专业模式'}</span>
      <button type="button" className="btn" onClick={() => onChange(mode === 'guided' ? 'advanced' : 'guided')}>
        {mode === 'guided' ? '专业视图' : '小白模式'}
      </button>
    </div>
  )
}

export function GuideSteps({ labels, current }: { labels: string[]; current: number }) {
  return (
    <ol className="guide-steps" aria-label="操作步骤">
      {labels.map((label, index) => (
        <li key={label} className={index < current ? 'done' : index === current ? 'current' : ''}>
          <span>{index + 1}</span>{label}
        </li>
      ))}
    </ol>
  )
}

const suggestions: Record<string, string> = {
  NOT_TRADING_DAY: '请选择交易日历中的开市日期。',
  NO_QUOTE_FOR_EXECUTION_DATE: '换一个有本地行情的日期或股票。',
  INVALID_TS_CODE: '请输入六位股票代码，例如 000001。',
  QTY_NOT_MULTIPLE_OF_LOT: '股票数量通常应为 100 股的整数倍。',
  INSUFFICIENT_CASH: '减少买入数量后重新预览。',
  CASH_BUFFER_LIMIT_EXCEEDED: '减少数量，至少保留 10% 现金。',
  GROSS_EXPOSURE_LIMIT_EXCEEDED: '减少数量，避免总持仓超过 80%。',
  DAILY_BUY_LIMIT_EXCEEDED: '减少数量，单日新增买入不能超过权益的 20%。',
  INSUFFICIENT_SELLABLE_QUANTITY: '只能卖出持仓中标记为“可卖”的数量。',
  DUPLICATE_ACTIVE_ORDER: '先处理该股票已有的待确认或待成交订单。',
}

export function friendlyError(error: unknown): { message: string; suggestion: string; technical: string } {
  if (error instanceof ApiError) {
    return {
      message: error.message,
      suggestion: suggestions[error.code] || (error.retryable ? '稍后重试。' : '检查输入后重新操作。'),
      technical: `${error.code} · HTTP ${error.status} · ${JSON.stringify(error.details)}`,
    }
  }
  const message = error instanceof Error ? error.message : String(error)
  return { message, suggestion: '检查输入后重新操作。', technical: message }
}

export function FriendlyError({ error }: { error: unknown }) {
  if (!error) return null
  const info = friendlyError(error)
  return (
    <div className="guide-feedback error" role="alert">
      <strong>{info.message}</strong>
      <span>{info.suggestion}</span>
      <details><summary>查看技术详情</summary><code>{info.technical}</code></details>
    </div>
  )
}

export function SuccessFeedback({ children }: { children: React.ReactNode }) {
  return <div className="guide-feedback success" role="status">✓ {children}</div>
}
