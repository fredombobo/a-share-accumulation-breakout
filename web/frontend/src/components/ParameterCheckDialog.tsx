import { useEffect, useRef } from 'react'

import type { BacktestPreview } from '../api/client'

export type ParameterCheckResult =
  | { kind: 'success'; preview: BacktestPreview }
  | { kind: 'error'; message: string }

function formatDate(value?: string): string {
  if (!value || value.length !== 8) return value || '不可用'
  return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}`
}

export default function ParameterCheckDialog({
  result,
  onClose,
  onViewPreview,
}: {
  result: ParameterCheckResult
  onClose: () => void
  onViewPreview: () => void
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null)
  const primaryRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (typeof dialog.showModal === 'function') {
      if (!dialog.open) dialog.showModal()
    } else {
      dialog.setAttribute('open', '')
    }
    primaryRef.current?.focus()
    return () => {
      if (dialog.open && typeof dialog.close === 'function') dialog.close()
      previousFocus?.focus()
    }
  }, [result])

  const success = result.kind === 'success'
  const preview = success ? result.preview : null

  return (
    <dialog
      ref={dialogRef}
      className={`backtest-dialog ${success ? 'success' : 'error'}`}
      aria-labelledby="parameter-check-title"
      aria-describedby="parameter-check-description"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <button className="dialog-close" type="button" aria-label="关闭参数检查结果" onClick={onClose}>×</button>
      <div className="dialog-heading">
        <span className="dialog-status-icon" aria-hidden="true">{success ? '✓' : '!'}</span>
        <div>
          <span className="guide-eyebrow">运行前检查</span>
          <h2 id="parameter-check-title">{success ? '参数检查通过' : '参数检查未通过'}</h2>
          <p id="parameter-check-description">
            {success
              ? '输入已形成冻结预览。检查通过不代表策略有效，也没有启动回测。'
              : result.message}
          </p>
        </div>
      </div>

      {preview ? (
        <>
          <div className="check-summary-grid">
            <div><span>有效组合</span><b>{preview.prepared.parameter_space.count}</b></div>
            <div><span>冻结股票</span><b>{preview.prepared.universe.count}</b></div>
            <div><span>动态预热</span><b>{preview.prepared.parameter_space.horizon} 日</b></div>
            <div>
              <span>研究范围</span>
              <b>{formatDate(preview.prepared.windows.is[0])} 至 {formatDate(preview.prepared.windows.oos[1])}</b>
            </div>
          </div>
          <div className={`check-runtime-note ${preview.estimated_work.long_running ? 'warning' : ''}`}>
            <b>{preview.estimated_work.long_running ? '预计为长耗时任务' : '可以进入运行确认'}</b>
            <span>{preview.estimated_work.note}</span>
          </div>
          <details className="check-technical-detail">
            <summary>查看冻结身份</summary>
            <div><span>参数空间</span><code>{preview.prepared.parameter_space.sha256}</code></div>
            <div><span>股票池</span><code>{preview.prepared.universe.sha256}</code></div>
            <div><span>输入</span><code>{preview.prepared.input_hash}</code></div>
          </details>
        </>
      ) : (
        <div className="check-error-help">
          <b>建议按顺序检查</b>
          <ol>
            <li>参数起点、终点和步长是否在允许范围内。</li>
            <li>板块或指定股票是否能形成有效股票池。</li>
            <li>调整后重新点击“检查参数空间”。</li>
          </ol>
        </div>
      )}

      <div className="dialog-actions">
        {preview ? (
          <>
            <button className="btn" type="button" onClick={onClose}>继续调整</button>
            <button ref={primaryRef} className="btn primary" type="button" onClick={onViewPreview}>查看冻结预览</button>
          </>
        ) : (
          <button ref={primaryRef} className="btn primary" type="button" onClick={onClose}>返回修改参数</button>
        )}
      </div>
    </dialog>
  )
}
