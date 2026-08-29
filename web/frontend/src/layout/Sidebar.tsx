import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { fetchPlatformStatus, type PlatformStatus } from '../api/platform'
import { IcoOverview, IcoPaper } from '../components/Icons'

const items = [
  { path: '/', label: '每日选股', hint: '扫描 · 候选 · 资金', Icon: IcoOverview },
  { path: '/paper', label: '纸面仿真', hint: '持仓 · 成交 · 对账', Icon: IcoPaper },
]

const readinessCopy: Record<string, string> = {
  BLOCKED: '门禁阻断',
  ENGINEERING_READY_RESEARCH_BLOCKED: '工程就绪 · 研究阻断',
  PERSONAL_INSTITUTIONAL_READY: '七门通过',
}

export default function Sidebar() {
  const nav = useNavigate()
  const loc = useLocation()
  const [platform, setPlatform] = useState<PlatformStatus | null>(null)

  useEffect(() => {
    let active = true
    fetchPlatformStatus()
      .then((status) => {
        if (active) setPlatform(status)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [])

  let active = '/'
  if (loc.pathname.startsWith('/paper')) active = '/paper'
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">A</span>
        <span>
          AB-Screener
          <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--faint)', marginTop: -2, letterSpacing: '0.04em' }}>
            ACCUMULATION · BREAKOUT
          </div>
        </span>
      </div>
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
          <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', minWidth: 0 }}>
            <span className="txt">{label}</span>
            <span className="hint">{hint}</span>
          </span>
        </button>
      ))}
      <div className="spacer" />
      <div className="sidebar-foot">
        <div style={{ marginBottom: 6 }}><span className="live-dot" />本地运行 · 127.0.0.1:8001</div>
        {platform && (
          <div style={{ marginBottom: 6 }} title={`build ${platform.build_version}`}>
            <b>就绪度</b> {readinessCopy[platform.readiness] || platform.readiness}
          </div>
        )}
        <b>每日</b> 更新 → 扫描 → 看证据<br />
        <b>仿真</b> 预览 → 成交 → 对账<br />
        <span style={{ display: 'block', marginTop: 6, opacity: 0.8 }}>只做纸面仿真，不连接券商</span>
      </div>
    </aside>
  )
}
