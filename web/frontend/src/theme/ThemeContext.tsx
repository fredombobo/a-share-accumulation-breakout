import React, { createContext, useContext, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

interface Ctx {
  theme: Theme
  toggle: () => void
}

const ThemeCtx = createContext<Ctx>({ theme: 'light', toggle: () => {} })

export const useTheme = () => useContext(ThemeCtx)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('ab-theme')
    return saved === 'light' || saved === 'dark' ? saved : 'light'
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
        text: '#e1e7ef',
        subtext: '#a1adbd',
        axis: '#435063',
        split: '#2b3543',
        up: '#ef8882',
        down: '#73c0a4',
        accent: '#91b2dd',
        accent2: '#83b5ac',
        warn: '#d1ae75',
        palette: ['#91b2dd', '#83b5ac', '#d1ae75', '#a8a0c4', '#89a0b3', '#d09b98', '#9ead8a', '#b8c1cf'],
      }
    }
    return {
      text: '#202c3d',
      subtext: '#647083',
      axis: '#cbd3de',
      split: '#e3e7ed',
      up: '#c95851',
      down: '#27816b',
      accent: '#355c88',
      accent2: '#4a827c',
      warn: '#b78840',
      palette: ['#355c88', '#4a827c', '#b78840', '#817798', '#728b9d', '#ba706a', '#81916d', '#a1adbb'],
    }
  }, [theme])
}
