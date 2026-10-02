import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, type TodayGuide } from '../api/client'
import { IcoLayers, IcoOverview, IcoPaper, IcoScan } from '../components/Icons'

const items = [
  { path: '/', label: '每日选股', hint: '扫描 · 候选 · 资金', Icon: IcoOverview },
  { path: '/backtest', label: '研究回测', hint: '多参数 · OOS · 成本', Icon: IcoLayers },
  { path: '/forward', label: '前瞻观察', hint: '冻结名单 · 后续表现', Icon: IcoScan },
]

const helpItem = { path: '/guide', label: '使用说明', hint: '逻辑 · 操作 · 术语', Icon: IcoPaper }

export default function Sidebar() {
  const nav = useNavigate()
  const loc = useLocation()
  const [today, setToday] = useState<TodayGuide | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let active = true
    const refresh = () => api.today()
      .then((status) => {
        if (active) { setToday(status); setUnavailable(false) }
      })
      .catch(() => { if (active) setUnavailable(true) })
    void refresh()
    const timer = setInterval(refresh, 30000)
    window.addEventListener('data-synced', refresh)
    return () => {
      active = false
      clearInterval(timer)
      window.removeEventListener('data-synced', refresh)
    }
  }, [])

  let active = '/'
  if (loc.pathname.startsWith('/backtest')) active = '/backtest'
  if (loc.pathname.startsWith('/guide')) active = '/guide'
  if (loc.pathname.startsWith('/forward')) active = '/forward'
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">AB<span /></span>
        <span className="brand-wordmark">AB-Screener<small>独立投资研究</small></span>
      </div>
      <div className="nav-caption">工作空间</div>
      <nav className="workspace-nav" aria-label="主导航">
      {items.map(({ path, label, hint, Icon }) => (
        <button
          type="button"
          key={path}
          className={`nav-item ${active === path ? 'active' : ''}`}
          onClick={() => nav(path)}
          title={hint}
          aria-current={active === path ? 'page' : undefined}
        >
          <span className="ico"><Icon size={17} /></span>
          <span className="nav-label">
            <span className="txt">{label}</span>
            <span className="hint">{hint}</span>
          </span>
        </button>
      ))}
      <div className="nav-sep" />
      <button
        type="button"
        className={`nav-item ${active === helpItem.path ? 'active' : ''}`}
        onClick={() => nav(helpItem.path)}
        title={helpItem.hint}
        aria-current={active === helpItem.path ? 'page' : undefined}
      >
        <span className="ico"><helpItem.Icon size={17} /></span>
        <span className="nav-label">
          <span className="txt">{helpItem.label}</span>
          <span className="hint">{helpItem.hint}</span>
        </span>
      </button>
      </nav>
      <div className="spacer" />
      <div className="sidebar-foot">
        <div className="workspace-status"><span className={`live-dot ${unavailable || !today ? 'unknown' : ''}`} />{unavailable ? '状态暂不可用' : today ? '本地研究空间' : '正在连接'}</div>
        <p title={today?.reason}>{unavailable ? '请稍后重试' : today?.title || '等待今日状态'}</p>
        <div className="sidebar-foot-meta"><span>仅供研究</span><span className="mono">LOCAL / 8001</span></div>
      </div>
    </aside>
  )
}
