import { FormEvent, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, LabStatusResp, SyncStatus } from '../api/client'
import { useTheme } from '../theme/ThemeContext'
import { IcoMoon, IcoPulse, IcoRefresh, IcoSearch, IcoSun } from '../components/Icons'

type PageMeta = { kicker: string; title: string; sub: string }

const V2_PAGE_META: Record<string, PageMeta> = {
  '/v2/desk': { kicker: 'V2 Desk', title: '指挥舱', sub: '今日唯一动作 · 门禁驱动' },
  '/v2/intelligence': { kicker: 'Market Intelligence', title: '市场情报', sub: '档案 · 宽度 · 数据状态' },
  '/v2/strategies': { kicker: 'Strategy Registry', title: '六形态', sub: '插件契约 · 研究状态' },
  '/v2/signals': { kicker: 'Signal Observatory', title: '信号观察', sub: '不可变观察 · 生命周期' },
  '/v2/research': { kicker: 'Research Governance', title: '研究治理', sub: '实验登记 · Trial Ledger' },
  '/v2/monitor': { kicker: 'Operations Monitor', title: '监控', sub: '系统健康 · 告警' },
  '/v2/review': { kicker: 'Decision Review', title: '复核', sub: '笔记 · 决策' },
  '/v2/system': { kicker: 'System', title: '系统', sub: '健康 · 备份 · 深检' },
  '/v2/compare': { kicker: 'Compare', title: '对比', sub: '2–6 标的 K 线' },
}

export function pageMeta(pathname: string): PageMeta {
  const v2Path = Object.keys(V2_PAGE_META).find(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  )
  if (v2Path) return V2_PAGE_META[v2Path]
  if (pathname.startsWith('/paper')) {
    return { kicker: 'Paper Trading', title: '纸面交易', sub: '仿真撮合 · 持仓 · 对账 · 不下单' }
  }
  if (pathname.startsWith('/lab')) {
    return { kicker: 'Research Lab', title: '策略实验室', sub: '验证策略是否值得继续研究 · 非下单' }
  }
  if (pathname.startsWith('/backtest')) {
    return { kicker: 'Backtest Studio', title: '回测工作台', sub: '自定义参数 · 净成本样本内/样本外' }
  }
  if (pathname.startsWith('/stock')) {
    return { kicker: 'Stock Detail', title: '个股详情', sub: 'K 线 · 箱体 · 资金流 · 交易卡片' }
  }
  return { kicker: 'Overview', title: '选股总览', sub: '技术形态 + 资金流 + 基本面 三层筛选' }
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
  const [labTask, setLabTask] = useState<LabStatusResp | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)

  useEffect(() => {
    let alive = true
    const refreshTask = () => {
      api.labStatus().then((task) => { if (alive) setLabTask(task) }).catch(() => undefined)
    }
    refreshTask()
    const timer = window.setInterval(refreshTask, 5_000)
    window.addEventListener('focus', refreshTask)
    return () => {
      alive = false
      window.clearInterval(timer)
      window.removeEventListener('focus', refreshTask)
    }
  }, [loc.pathname])

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
        {labTask?.task_id && ['pending', 'running', 'cancelling'].includes(labTask.status) && (
          <button type="button" className="lab-global-task" onClick={() => navigate('/lab')}>
            <IcoPulse size={13} />
            实验进行中 {labTask.progress || 0}%
          </button>
        )}
        {labTask?.task_id && labTask.status === 'done' && (
          <button
            type="button"
            className="lab-global-task done"
            onClick={() => navigate('/lab?view=results#lab-conclusion', {
              state: { openLabConclusion: true, requestedAt: Date.now() },
            })}
          >
            实验已完成 · 查看结论
          </button>
        )}
        {/* 数据新鲜度 + 手动更新 */}
        <div className="sync-capsule" title={syncing ? sync?.message : `最新行情日期：${sync?.latest_daily || '—'}`}>
          <span className={`sync-dot ${syncing ? 'spin' : ''}`} />
          <span className="sync-text num">
            {syncing ? '同步行情中…' : (sync?.latest_daily ? `数据 ${sync.latest_daily}` : '数据 —')}
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
