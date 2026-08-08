import { useLocation, useNavigate } from 'react-router'

const items = [
  { path: '/', label: '📈 选股总览', hint: 'A池可交易' },
  { path: '/lab', label: '🧪 策略实验室', hint: '研究 · 非下单' },
  { path: '/paper', label: '📋 纸面交易', hint: '仿真 · 不下单' },
]

export default function Sidebar() {
  const nav = useNavigate()
  const loc = useLocation()
  let active = '/'
  if (loc.pathname.startsWith('/stock')) active = '/stock'
  else if (loc.pathname.startsWith('/lab')) active = '/lab'
  else if (loc.pathname.startsWith('/paper')) active = '/paper'
  return (
    <aside className="sidebar">
      <div className="brand">📊 AB-Screener</div>
      {items.map((it) => (
        <button
          type="button"
          key={it.path}
          className={`nav-item ${active === it.path ? 'active' : ''}`}
          onClick={() => nav(it.path)}
          title={it.hint}
          aria-current={active === it.path ? 'page' : undefined}
        >
          {it.label}
          <div style={{ fontSize: 10, opacity: 0.7, marginTop: 2 }}>{it.hint}</div>
        </button>
      ))}
      <div className="spacer" />
      <div className="muted" style={{ fontSize: 11, padding: '0 12px', lineHeight: 1.45 }}>
        <b>总览</b>：扫描 → A 池<br />
        <b>实验室</b>：参数摸底<br />
        <b>纸面</b>：仿真交易闭环<br />
        三者勿混用
      </div>
    </aside>
  )
}
