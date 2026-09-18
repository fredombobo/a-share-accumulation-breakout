import { FormEvent, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, SyncStatus, type StockSearchMatch } from '../api/client'
import { useTheme } from '../theme/ThemeContext'
import { IcoMoon, IcoRefresh, IcoSearch, IcoSun } from '../components/Icons'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'

type PageMeta = { kicker: string; title: string; sub: string }

export function pageMeta(pathname: string): PageMeta {
  if (pathname.startsWith('/forward')) return { kicker: 'Prospective Observation', title: '前瞻观察', sub: '冻结名单 · 逐日追踪 · 证据修订' }
  if (pathname.startsWith('/guide')) {
    return { kicker: 'Operating Manual', title: '使用说明', sub: '每日选股 · 研究回测 · 分类标准' }
  }
  if (pathname.startsWith('/backtest')) {
    return { kicker: 'Research Backtest', title: '研究回测', sub: '参数空间 · 样本外 · 成本压力' }
  }
  if (pathname.startsWith('/stock')) {
    return { kicker: 'Stock Detail', title: '个股详情', sub: '价格结构 · 财务摘要 · 研究证据' }
  }
  return { kicker: 'Daily Workflow', title: '每日选股', sub: '更新行情 · 扫描候选 · 核对证据' }
}

export function normalizeTsCode(input: string): string | null {
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
  const loc = useLocation()
  const meta = pageMeta(loc.pathname)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncUnavailable, setSyncUnavailable] = useState(false)
  const [searching, setSearching] = useState(false)
  const [matches, setMatches] = useState<StockSearchMatch[]>([])
  const searchRevision = useRef(0)
  const runningRef = useRef(false)

  // 数据同步状态轮询：idle 时慢速、running 时快速；完成后广播事件让页面刷新
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = () => {
      api.syncStatus()
        .then((st) => {
          if (!alive) return
          const wasRunning = runningRef.current
          runningRef.current = st.status === 'running'
          setSync(st)
          setSyncUnavailable(false)
          if (wasRunning && st.status !== 'running') {
            window.dispatchEvent(new CustomEvent('data-synced', { detail: st }))
          }
        })
        .catch(() => { if (alive) setSyncUnavailable(true) })
        .finally(() => {
          if (alive) timer = setTimeout(tick, runningRef.current ? 2000 : 15000)
        })
    }
    tick()
    return () => { alive = false; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSync = async () => {
    try {
      await api.syncStart()
      runningRef.current = true
      setSync({ status: 'running', message: '开始同步…', started_at: null, finished_at: null, latest_daily: null, latest_moneyflow: null, failed_dates: [] })
      window.dispatchEvent(new Event(RUN_TASK_EVENT))
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => () => { searchRevision.current += 1 }, [])

  const openStock = (code: string) => {
    setError('')
    setMatches([])
    navigate(`/stock/${encodeURIComponent(code)}`)
  }

  const onSearch = async (event: FormEvent) => {
    event.preventDefault()
    const code = normalizeTsCode(query)
    if (code) { openStock(code); return }
    const term = query.trim()
    if (!term) { setError('请输入股票名称或六位代码'); return }
    if (/^[\d.\s]+$/.test(term)) { setError('股票代码应为六位数字，例如 000001'); return }
    const revision = ++searchRevision.current
    setSearching(true)
    setError('')
    setMatches([])
    try {
      const stocks = await api.searchStocks(term.replace(/\s/g, ''))
      if (revision !== searchRevision.current) return
      const compact = (value: string) => value.replace(/\s/g, '').toUpperCase()
      const exact = stocks.filter(stock => compact(stock.name) === compact(term))
      if (exact.length === 1) { openStock(exact[0].ts_code); return }
      const found = stocks.filter(stock => compact(stock.name).includes(compact(term)) || stock.ts_code.includes(term.toUpperCase())).slice(0, 8)
      setMatches(found)
      if (!found.length) setError('本地股票目录中未找到匹配项，可尝试六位代码')
    } catch {
      if (revision === searchRevision.current) setError('股票目录暂不可用，可直接输入六位代码查询')
    } finally {
      if (revision === searchRevision.current) setSearching(false)
    }
  }

  const syncing = sync?.status === 'running'

  return (
    <header className="topbar">
      <div className="page-heading" title={meta.sub}>
        <span className="topbar-section">研究空间 <span aria-hidden="true">/</span></span>
        <span className="topbar-title">{meta.title}</span>
      </div>
      <div className="right">
        {/* 数据新鲜度 + 手动更新 */}
        <div className="sync-capsule" title={syncing ? sync?.message : `最新行情日期：${sync?.latest_daily || '—'}`}>
          <span className={`sync-dot ${syncing ? 'spin' : syncUnavailable || !sync ? 'unknown' : ''}`} />
          <span className="sync-text num">
            {syncing ? '同步行情中…' : syncUnavailable ? '同步状态待确认' : (sync?.latest_daily ? `行情 ${sync.latest_daily.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1.$2.$3')}` : '行情待确认')}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onSync}
            disabled={syncing}
            title="手动更新行情（增量同步，约 1~5 分钟）"
          >
            <IcoRefresh size={12} />更新
          </button>
        </div>
        <form className="stock-search" onSubmit={onSearch} role="search" aria-label="查询个股">
          <div className="stock-search-field">
            <span className="search-icon">
              <IcoSearch size={14} />
            </span>
            <input
              className="search"
              placeholder="搜索股票名称 / 代码"
              maxLength={80}
              value={query}
              onChange={(e) => { searchRevision.current += 1; setSearching(false); setQuery(e.target.value); setError(''); setMatches([]) }}
              onKeyDown={(e) => { if (e.key === 'Escape') { setMatches([]); setError('') } }}
              aria-label="股票名称或代码"
              aria-describedby={error ? 'stock-search-error' : undefined}
            />
          </div>
          <button className="search-submit" type="submit" disabled={searching}>{searching ? '查询中' : '查询'}</button>
          {error && <div className="search-feedback" id="stock-search-error" role="alert">{error}</div>}
          {!!matches.length && <div className="search-results" aria-label="匹配股票"><p>选择股票 · 最多显示 8 项</p>{matches.map(stock => <button key={stock.ts_code} type="button" onClick={() => openStock(stock.ts_code)}><span>{stock.name}<small>{stock.industry || '—'}</small></span><span className="mono">{stock.ts_code}</span></button>)}</div>}
        </form>
        <button className="btn-icon theme-toggle" onClick={toggle} aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'} title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}>
          {theme === 'dark' ? <IcoSun size={16} /> : <IcoMoon size={16} />}
        </button>
      </div>
    </header>
  )
}
