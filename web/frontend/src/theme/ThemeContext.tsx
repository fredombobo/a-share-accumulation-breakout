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
        text: '#e9eef7',
        subtext: '#93a1b8',
        axis: '#243048',
        split: '#1b2434',
        up: '#ff5a5f',
        down: '#2ebd85',
        accent: '#3e9dff',
        accent2: '#2ad4c3',
        warn: '#f5a623',
        palette: ['#3e9dff', '#2ad4c3', '#a78bfa', '#f5a623', '#34d399', '#f472b6', '#60a5fa', '#fb923c', '#c084fc'],
      }
    }
    return {
      text: '#101828',
      subtext: '#4a586e',
      axis: '#cbd5e1',
      split: '#e6ebf2',
      up: '#e0453f',
      down: '#0a8f5c',
      accent: '#2f6bff',
      accent2: '#0e9384',
      warn: '#c77d0b',
      palette: ['#2f6bff', '#0e9384', '#7c3aed', '#c77d0b', '#0a8f5c', '#db2777', '#4f46e5', '#ea580c', '#9333ea'],
    }
  }, [theme])
}
