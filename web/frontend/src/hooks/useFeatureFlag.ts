import { useCallback, useState } from 'react'

/** 特性开关：与后端平台配置 V2_* flags 对应；前端本地默认可经 URL ?flag=1 覆盖。 */
const FLAG_WHITELIST = [
  'V2_PIT_READ_ENABLED',
  'V2_EXECUTION_DUAL_RUN_ENABLED',
  'V2_EXECUTION_WRITE_ENABLED',
  'V2_STRATEGY_REGISTRY_ENABLED',
  'V2_RISK_ENFORCEMENT_ENABLED',
  'DAILY_SCHEDULER_ENABLED',
  'INSTITUTIONAL_CONSOLE_V2_ENABLED',
] as const

export type FeatureFlag = (typeof FLAG_WHITELIST)[number]

const LOCAL_KEY = 'ab_feature_flags'

function readLocal(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}')
  } catch {
    return {}
  }
}

export function useFeatureFlag() {
  const [overrides, setOverrides] = useState<Record<string, boolean>>(readLocal)

  const enabled = useCallback(
    (flag: FeatureFlag): boolean => {
      if (flag in overrides) return overrides[flag]
      // 支持 URL ?flag=1 调试覆盖
      const urlFlag = new URLSearchParams(window.location.search).get(flag)
      if (urlFlag != null) return urlFlag === '1' || urlFlag === 'true'
      return false
    },
    [overrides],
  )

  const setFlag = useCallback((flag: FeatureFlag, value: boolean) => {
    setOverrides((prev) => {
      const next = { ...prev, [flag]: value }
      localStorage.setItem(LOCAL_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  return { enabled, setFlag }
}
