import type { ApiErrorState } from '../../hooks/useApiError'

/** 结构化 API 错误面板：默认展示「原因 + 解决方式」，技术细节折叠。 */
export function ApiErrorPanel({
  error,
  onRetry,
}: {
  error: ApiErrorState
  onRetry?: () => void
}) {
  const retryable = error.retryable || error.status >= 500
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium">
            {error.message || `请求失败（HTTP ${error.status}）`}
          </div>
          {error.code !== 'UNKNOWN' && (
            <div className="mt-0.5 font-mono text-xs text-red-500">
              错误码 {error.code}
            </div>
          )}
        </div>
        {retryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
          >
            重试
          </button>
        )}
      </div>
    </div>
  )
}
