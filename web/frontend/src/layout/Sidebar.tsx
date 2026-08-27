import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { fetchPlatformStatus, type PlatformStatus } from '../api/platform'
import { IcoLab, IcoOverview, IcoPaper, IcoTarget } from '../components/Icons'

const items = [
  { path: '/', label: '选股总览', hint: 'A 池可交易 · B 池观察', Icon: IcoOverview },
  { path: '/backtest', label: '回测工作台', hint: '自定义参数 · IS/OOS', Icon: IcoTarget },
  { path: '/lab', label: '策略实验室', hint: '可信验证 · 非下单', Icon: IcoLab },
  { path: '/paper', label: '纸面交易', hint: '仿真撮合 · 不下单', Icon: IcoPaper },
]

// v2 控制台导航（P7.3）：指挥舱 / 情报 / 六形态 / 信号
const v2Items = [
  { path: '/v2/desk', label: '指挥舱', hint: '今日唯一动作', Icon: IcoTarget },
  { path: '/v2/intelligence', label: '市场情报', hint: '档案 · 宽度 · 数据状态', Icon: IcoOverview },
  { path: '/v2/strategies', label: '六形态', hint: '插件契约 · 研究状态', Icon: IcoLab },
  { path: '/v2/signals', label: '信号观察', hint: '不可变观察 · 生命周期', Icon: IcoPaper },
  { path: '/v2/research', label: '研究治理', hint: '实验登记 · trial ledger', Icon: IcoLab },
  { path: '/v2/monitor', label: '监控', hint: '系统健康 · 告警', Icon: IcoOverview },
  { path: '/v2/review', label: '复核', hint: '笔记 · 决策', Icon: IcoPaper },
  { path: '/v2/system', label: '系统', hint: '健康 · 备份 · 深检', Icon: IcoTarget },
  { path: '/v2/compare', label: '对比', hint: '2–6 标的 K 线', Icon: IcoLab },
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
  const [platformUnavailable, setPlatformUnavailable] = useState(false)

  useEffect(() => {
    let active = true
    fetchPlatformStatus()
      .then((status) => {
        if (active) setPlatform(status)
      })
      .catch(() => {
        if (active) setPlatformUnavailable(true)
      })
    return () => {
      active = false
    }
  }, [])

  // Fail closed when a stale proxy/mock returns a partial platform payload.
  // A missing flags object must hide privileged navigation, not crash the app.
  const v2Enabled = platform?.flags?.INSTITUTIONAL_CONSOLE_V2_ENABLED === true
  const strategiesEnabled = platform?.flags?.V2_STRATEGY_REGISTRY_ENABLED === true
  let active = '/'
  if (loc.pathname.startsWith('/stock')) active = '/stock'
  else if (loc.pathname.startsWith('/backtest')) active = '/backtest'
  else if (loc.pathname.startsWith('/lab')) active = '/lab'
  else if (loc.pathname.startsWith('/paper')) active = '/paper'
  else if (loc.pathname.startsWith('/v2')) active = loc.pathname.split('/').slice(0, 3).join('/')
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
      <div className="nav-sep">
        {v2Enabled ? 'V2 控制台' : platformUnavailable ? 'V2 状态不可用' : '正在核对 V2 权限…'}
      </div>
      {v2Enabled && v2Items.map(({ path, label, hint, Icon }) => {
        const registryReadOnly = !strategiesEnabled && ['/v2/strategies', '/v2/signals'].includes(path)
        const resolvedHint = registryReadOnly ? `${hint} · 只读` : hint
        return (
        <button
          type="button"
          key={path}
          className={`nav-item ${active === path ? 'active' : ''}`}
          onClick={() => nav(path)}
          title={resolvedHint}
          aria-current={active === path ? 'page' : undefined}
        >
          <span className="ico"><Icon size={17} /></span>
          <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', minWidth: 0 }}>
            <span className="txt">{label}</span>
            <span className="hint">{resolvedHint}</span>
          </span>
        </button>
        )
      })}
      <div className="spacer" />
      <div className="sidebar-foot">
        <div style={{ marginBottom: 6 }}><span className="live-dot" />本地运行 · 127.0.0.1:8001</div>
        {platform && (
          <div style={{ marginBottom: 6 }} title={`build ${platform.build_version}`}>
            <b>就绪度</b> {readinessCopy[platform.readiness] || platform.readiness}
          </div>
        )}
        <b>总览</b> 扫描 → A 池<br />
        <b>工作台</b> 自定义回测<br />
        <b>实验室</b> 可信验证<br />
        <b>纸面</b> 仿真闭环<br />
        <span style={{ display: 'block', marginTop: 6, opacity: 0.8 }}>研究辅助，不是投资建议</span>
      </div>
    </aside>
  )
}
