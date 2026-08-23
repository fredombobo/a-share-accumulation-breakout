import { useCallback, useState } from 'react'

/**
 * 本地 UI 偏好（非业务旗标）。
 *
 * 安全约束：业务旗标（执行写、风险 enforce、调度、PIT 读、控制台等）必须由服务端
 * `/api/v2/platform/status` 下发，前端不得经 query string 或 localStorage 打开
 * 服务端已关闭的能力。本地只允许保存纯展示偏好（引导/专业视图）。
 */
const UI_PREF_KEYS = ['GUIDED_MODE', 'PRO_VIEW'] as const

export type UiPreference = (typeof UI_PREF_KEYS)[number]

const LOCAL_KEY = 'ab_ui_preferences'

function readLocal(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}')
  } catch {
    return {}
  }
}

export function useFeatureFlag() {
  const [overrides, setOverrides] = useState<Record<string, boolean>>(readLocal)

  const enabled = useCallback((flag: UiPreference): boolean => {
    if (flag in overrides) return overrides[flag]
    return false
  }, [overrides])

  const setFlag = useCallback((flag: UiPreference, value: boolean) => {
    setOverrides((prev) => {
      const next = { ...prev, [flag]: value }
      localStorage.setItem(LOCAL_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  return { enabled, setFlag }
}
