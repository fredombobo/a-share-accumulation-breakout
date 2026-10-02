import { lazy, Suspense, useLayoutEffect, useRef } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import Sidebar from './layout/Sidebar'
import Topbar from './layout/Topbar'
import GlobalRunProgress from './components/GlobalRunProgress'

const Overview = lazy(() => import('./pages/Overview'))
const StockDetail = lazy(() => import('./pages/StockDetail'))
const ProfessionalBacktest = lazy(() => import('./pages/ProfessionalBacktest'))
const Guide = lazy(() => import('./pages/Guide'))
const ForwardObservations = lazy(() => import('./pages/ForwardObservations'))

// 龙虎榜研究页面：保留路由与构建产物（8123 隔离产品要用同一份 dist），
// 但不进 Sidebar 导航 —— 研究状态仍为 RESEARCH_BLOCKED，不混进日用闭环。
const LhbRadar = lazy(() => import('./pages/v2/LhbRadar'))
const LhbProfile = lazy(() => import('./pages/v2/LhbProfile'))
const LhbStockTimeline = lazy(() => import('./pages/v2/LhbStockTimeline'))
const LhbNetwork = lazy(() => import('./pages/v2/LhbNetwork'))
const LhbQuality = lazy(() => import('./pages/v2/LhbQuality'))
const LhbBacktest = lazy(() => import('./pages/v2/LhbBacktest'))

export default function App() {
  const { pathname } = useLocation()
  const contentRef = useRef<HTMLElement>(null)
  useLayoutEffect(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: 'instant' })
  }, [pathname])
  return (
    <div className="app">
      <a className="skip-link" href="#main-content">跳转到主要内容</a>
      <Sidebar />
      <div className="main">
        <Topbar />
        <GlobalRunProgress />
        <main className="content" id="main-content" ref={contentRef} tabIndex={-1}>
          <Suspense fallback={<div className="page-loading" role="status"><span className="loading-line" /><p>正在载入研究空间</p><span className="muted">准备页面与数据视图…</span></div>}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/stock/:tsCode" element={<StockDetail />} />
              <Route path="/backtest" element={<ProfessionalBacktest />} />
              <Route path="/guide" element={<Guide />} />
              <Route path="/forward" element={<ForwardObservations />} />
              <Route path="/v2/lhb/radar" element={<LhbRadar />} />
              <Route path="/v2/lhb/profile" element={<LhbProfile />} />
              <Route path="/v2/lhb/timeline" element={<LhbStockTimeline />} />
              <Route path="/v2/lhb/network" element={<LhbNetwork />} />
              <Route path="/v2/lhb/quality" element={<LhbQuality />} />
              <Route path="/v2/lhb/backtest" element={<LhbBacktest />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  )
}
