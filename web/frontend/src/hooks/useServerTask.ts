import { useCallback, useEffect, useRef, useState } from 'react'

export interface ServerTaskState<T> {
  data: T | null
  loading: boolean
  error: string | null
  refresh: () => Promise<T | null>
}

/**
 * 服务端任务 hook：封装轮询型长任务（扫描/研究运行）。
 * poll 返回 done=true 时停止轮询；组件卸载/切页后自动停止，状态以服务端为准。
 */
export function useServerTask<T>(
  fetcher: () => Promise<T>,
  isDone: (data: T) => boolean,
  options?: { intervalMs?: number; enabled?: boolean },
): ServerTaskState<T> {
  const { intervalMs = 2000, enabled = true } = options || {}
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const fetcherRef = useRef(fetcher)
  const isDoneRef = useRef(isDone)
  fetcherRef.current = fetcher
  isDoneRef.current = isDone
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async (): Promise<T | null> => {
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
      setLoading(false)
      if (isDoneRef.current(result) && timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
      return result
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setLoading(false)
      return null
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    setLoading(true)
    void refresh()
    timerRef.current = setInterval(() => void refresh(), intervalMs)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs])

  return { data, loading, error, refresh }
}
