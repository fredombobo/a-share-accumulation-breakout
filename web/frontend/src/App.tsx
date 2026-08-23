import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import Sidebar from './layout/Sidebar'
import Topbar from './layout/Topbar'

const Overview = lazy(() => import('./pages/Overview'))
const StockDetail = lazy(() => import('./pages/StockDetail'))
const StrategyLab = lazy(() => import('./pages/StrategyLab'))
const PaperTrading = lazy(() => import('./pages/PaperTrading'))
const BacktestStudio = lazy(() => import('./pages/BacktestStudio'))

// v2 控制台页面（P7.3）
const V2Desk = lazy(() => import('./pages/v2/Desk'))
const V2Intelligence = lazy(() => import('./pages/v2/Intelligence'))
const V2Strategies = lazy(() => import('./pages/v2/Strategies'))
const V2Signals = lazy(() => import('./pages/v2/Signals'))
const V2Research = lazy(() => import('./pages/v2/Research'))
const V2Monitor = lazy(() => import('./pages/v2/Monitor'))
const V2Review = lazy(() => import('./pages/v2/Review'))
const V2System = lazy(() => import('./pages/v2/System'))
const V2Compare = lazy(() => import('./pages/v2/Compare'))

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <div className="main">
        <Topbar />
        <div className="content">
          <Suspense fallback={<div className="loading">正在加载页面…</div>}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/stock/:tsCode" element={<StockDetail />} />
              <Route path="/lab" element={<StrategyLab />} />
              <Route path="/backtest" element={<BacktestStudio />} />
              <Route path="/paper" element={<PaperTrading />} />
              <Route path="/v2/desk" element={<V2Desk />} />
              <Route path="/v2/intelligence" element={<V2Intelligence />} />
              <Route path="/v2/strategies" element={<V2Strategies />} />
              <Route path="/v2/signals" element={<V2Signals />} />
              <Route path="/v2/research" element={<V2Research />} />
              <Route path="/v2/monitor" element={<V2Monitor />} />
              <Route path="/v2/review" element={<V2Review />} />
              <Route path="/v2/system" element={<V2System />} />
              <Route path="/v2/compare" element={<V2Compare />} />
              <Route path="*" element={<Overview />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </div>
  )
}
