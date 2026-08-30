import { FormEvent, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, SyncStatus } from '../api/client'
import { useTheme } from '../theme/ThemeContext'
import { IcoMoon, IcoRefresh, IcoSearch, IcoSun } from '../components/Icons'
import { RUN_TASK_EVENT } from '../components/GlobalRunProgress'

type PageMeta = { kicker: string; title: string; sub: string }

export function pageMeta(pathname: string): PageMeta {
  if (pathname.startsWith('/guide')) {
    return { kicker: 'Operating Manual', title: '使用说明', sub: '每日选股 · 专业回测 · 分类标准' }
  }
  if (pathname.startsWith('/backtest')) {
    return { kicker: 'Professional Research', title: '专业回测', sub: '参数空间 · 样本外 · 成本压力' }
  }
  if (pathname.startsWith('/stock')) {
    return { kicker: 'Stock Detail', title: '个股详情', sub: 'K 线 · 箱体 · 资金流 · 交易卡片' }
  }
  return { kicker: 'Daily Workflow', title: '每日选股', sub: '更新行情 · 扫描候选 · 核对证据' }
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
  const loc = useLocation()
  const meta = pageMeta(loc.pathname)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [sync, setSync] = useState<SyncStatus | null>(null)

  // 数据同步状态轮询：idle 时慢速、running 时快速；完成后广播事件让页面刷新
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = () => {
      api.syncStatus()
        .then((st) => {
          if (!alive) return
          const wasRunning = sync?.status === 'running'
          setSync(st)
          if (wasRunning && st.status !== 'running') {
            window.dispatchEvent(new CustomEvent('data-synced', { detail: st }))
          }
        })
        .catch(() => undefined)
        .finally(() => {
          if (alive) timer = setTimeout(tick, sync?.status === 'running' ? 2000 : 15000)
        })
    }
    tick()
    return () => { alive = false; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSync = async () => {
    try {
      await api.syncStart()
      setSync({ status: 'running', message: '开始同步…', started_at: null, finished_at: null, latest_daily: null, latest_moneyflow: null, failed_dates: [] })
      window.dispatchEvent(new Event(RUN_TASK_EVENT))
    } catch (e) {
      setError(String(e))
    }
  }

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

  const syncing = sync?.status === 'running'

  return (
    <header className="topbar">
      <div>
        <div className="kicker">{meta.kicker}</div>
        <h1>{meta.title}</h1>
        <div className="asof">{meta.sub}</div>
      </div>
      <div className="right">
        {/* 数据新鲜度 + 手动更新 */}
        <div className="sync-capsule" title={syncing ? sync?.message : `最新行情日期：${sync?.latest_daily || '—'}`}>
          <span className={`sync-dot ${syncing ? 'spin' : ''}`} />
          <span className="sync-text num">
            {syncing ? '同步行情中…' : (sync?.latest_daily ? `数据 ${sync.latest_daily}` : '数据 -')}
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
        <form className="stock-search" onSubmit={onSearch} title="跳转个股详情">
          <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
            <span style={{ position: 'absolute', left: 11, color: 'var(--faint)', display: 'grid', placeItems: 'center' }}>
              <IcoSearch size={14} />
            </span>
            <input
              className="search"
              style={{ paddingLeft: 32 }}
              placeholder="股票代码 000001 / 000001.SZ"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setError('') }}
              aria-label="股票代码搜索"
            />
          </div>
          <button className="btn btn-sm" type="submit">查询</button>
          {error && <span className="err-inline">{error}</span>}
        </form>
        <button className="btn-icon" onClick={toggle} title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}>
          {theme === 'dark' ? <IcoSun size={16} /> : <IcoMoon size={16} />}
        </button>
      </div>
    </header>
  )
}
