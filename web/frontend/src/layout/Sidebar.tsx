import { useLocation, useNavigate } from 'react-router'
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
]

const lhbItems = [
  { path: '/v2/lhb/radar', label: '龙虎榜雷达', hint: '每日事件 · 研究观察', Icon: IcoOverview },
  { path: '/v2/lhb/profile', label: '席位画像', hint: '收缩胜率 · 身份假设', Icon: IcoLab },
  { path: '/v2/lhb/timeline', label: '股票时间线', hint: '上榜轨迹', Icon: IcoTarget },
  { path: '/v2/lhb/network', label: '协同网络', hint: 'actor 去重投票', Icon: IcoPaper },
  { path: '/v2/lhb/quality', label: '数据质量', hint: '空/未发布/失败', Icon: IcoOverview },
  { path: '/v2/lhb/backtest', label: '回测 Shadow', hint: '非 edge 声明', Icon: IcoTarget },
]

export default function Sidebar() {
  const nav = useNavigate()
  const loc = useLocation()
  let active = '/'
  if (loc.pathname.startsWith('/stock')) active = '/stock'
  else if (loc.pathname.startsWith('/backtest')) active = '/backtest'
  else if (loc.pathname.startsWith('/lab')) active = '/lab'
  else if (loc.pathname.startsWith('/paper')) active = '/paper'
  else if (loc.pathname.startsWith('/v2/lhb')) active = loc.pathname
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
      <div className="nav-sep">V2 控制台</div>
      {v2Items.map(({ path, label, hint, Icon }) => (
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
      <div className="nav-sep">龙虎榜研究</div>
      {lhbItems.map(({ path, label, hint, Icon }) => (
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
        <b>总览</b> 扫描 → A 池<br />
        <b>工作台</b> 自定义回测<br />
        <b>实验室</b> 可信验证<br />
        <b>纸面</b> 仿真闭环<br />
        <span style={{ display: 'block', marginTop: 6, opacity: 0.8 }}>研究辅助，不是投资建议</span>
      </div>
    </aside>
  )
}
