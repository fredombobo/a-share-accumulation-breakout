import { useCallback, useState } from 'react'
import { ApiError } from '../api/core'

export interface ApiErrorState {
  code: string
  message: string
  retryable: boolean
  status: number
}

function toErrorState(err: unknown): ApiErrorState {
  if (err instanceof ApiError) {
    return {
      code: err.code,
      message: err.message,
      retryable: err.retryable,
      status: err.status,
    }
  }
  if (err instanceof Error) {
    return { code: 'UNKNOWN', message: err.message, retryable: false, status: 0 }
  }
  return { code: 'UNKNOWN', message: String(err), retryable: false, status: 0 }
}

/** 统一 API 错误状态：run(fn) 执行异步，错误结构化为可展示状态。 */
export function useApiError() {
  const [error, setError] = useState<ApiErrorState | null>(null)
  const clear = useCallback(() => setError(null), [])

  const run = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | null> => {
      setError(null)
      try {
        return await fn()
      } catch (err) {
        setError(toErrorState(err))
        return null
      }
    },
    [],
  )

  return { error, setError, clear, run }
}
