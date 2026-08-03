import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'
import { useTheme } from '../theme/ThemeContext'

const titles: Record<string, string> = {
  '': '选股总览', stock: '个股详情',
}

function normalizeTsCode(input: string): string | null {
  let raw = input.trim().toUpperCase().replace(/\s+/g, '')
  if (!raw) return null
  if (/^\d{6}$/.test(raw)) {
    if (raw.startsWith('6')) raw = `${raw}.SH`
    else if (raw.startsWith('4') || raw.startsWith('8') || raw.startsWith('9')) raw = `${raw}.BJ`
    else raw = `${raw}.SZ`
  }
  if (!/^\d{6}\.(SH|SZ|BJ)$/.test(raw)) return null
  return raw
}

export default function Topbar() {
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')

  const onSearch = (event: FormEvent) => {
    event.preventDefault()
    const code = normalizeTsCode(query)
    if (!code) {
      setError('格式：000001 或 000001.SZ / 600000.SH')
      return
    }
    setError('')
    navigate(`/stock/${encodeURIComponent(code)}`)
  }

  return (
    <header className="topbar">
      <div>
        <h1>横盘吸筹 → 启动 选股终端</h1>
        <div className="asof">技术形态 + 资金流 + 基本面 三层筛选</div>
      </div>
      <div className="right">
        <form className="stock-search" onSubmit={onSearch} title="跳转个股详情">
          <input
            className="search"
            placeholder="股票代码 000001 / 000001.SZ"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setError('') }}
            aria-label="股票代码搜索"
          />
          <button className="btn" type="submit">查询</button>
          {error && <span className="err-inline">{error}</span>}
        </form>
        <button className="theme-toggle" onClick={toggle} title="切换深浅主题">
          {theme === 'dark' ? '☀' : '◐'}
        </button>
      </div>
    </header>
  )
}
