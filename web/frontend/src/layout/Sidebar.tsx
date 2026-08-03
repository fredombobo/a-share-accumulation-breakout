import { useLocation, useNavigate } from 'react-router'

const items = [
  { path: '/', label: '📈 选股总览' },
]

export default function Sidebar() {
  const nav = useNavigate()
  const loc = useLocation()
  let active = '/'
  if (loc.pathname.startsWith('/stock')) active = '/stock'
  return (
    <aside className="sidebar">
      <div className="brand">📊 AB-Screener</div>
      {items.map((it) => (
        <div
          key={it.path}
          className={`nav-item ${active === it.path ? 'active' : ''}`}
          onClick={() => nav(it.path)}
        >
          {it.label}
        </div>
      ))}
      <div className="spacer" />
      <div className="muted" style={{ fontSize: 11, padding: '0 12px' }}>
        A池可交易 / B池观察<br />
        环境过滤 · 交易卡片
      </div>
    </aside>
  )
}
