import React, { createContext, useContext, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

interface Ctx {
  theme: Theme
  toggle: () => void
}

const ThemeCtx = createContext<Ctx>({ theme: 'dark', toggle: () => {} })

export const useTheme = () => useContext(ThemeCtx)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('ab-theme')
    return saved === 'light' || saved === 'dark' ? saved : 'dark'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('ab-theme', theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  return <ThemeCtx.Provider value={{ theme, toggle }}>{children}</ThemeCtx.Provider>
}

export interface ChartColors {
  text: string
  subtext: string
  axis: string
  split: string
  up: string
  down: string
  accent: string
  accent2: string
  warn: string
  palette: string[]
}

export function useChartColors(): ChartColors {
  const { theme } = useTheme()
  // useMemo：颜色对象按 theme 稳定，避免每次 render 新对象击穿下游 useMemo
  return React.useMemo(() => {
    if (theme === 'dark') {
      return {
        text: '#e6edf6',
        subtext: '#8a98ad',
        axis: '#2a3650',
        split: '#1b2536',
        up: '#ff4d4f',
        down: '#1bbf83',
        accent: '#3b82f6',
        accent2: '#22d3ee',
        warn: '#f59e0b',
        palette: ['#3b82f6', '#22d3ee', '#a78bfa', '#f59e0b', '#34d399', '#f472b6', '#60a5fa', '#fb923c', '#c084fc'],
      }
    }
    return {
      text: '#0f172a',
      subtext: '#64748b',
      axis: '#cbd5e1',
      split: '#e2e8f0',
      up: '#e23b3b',
      down: '#16a34a',
      accent: '#2563eb',
      accent2: '#0891b2',
      warn: '#d97706',
      palette: ['#2563eb', '#0891b2', '#7c3aed', '#d97706', '#059669', '#db2777', '#4f46e5', '#ea580c', '#9333ea'],
    }
  }, [theme])
}
