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
const LhbRadar = lazy(() => import('./pages/v2/LhbRadar'))
const LhbProfile = lazy(() => import('./pages/v2/LhbProfile'))
const LhbStockTimeline = lazy(() => import('./pages/v2/LhbStockTimeline'))
const LhbNetwork = lazy(() => import('./pages/v2/LhbNetwork'))
const LhbQuality = lazy(() => import('./pages/v2/LhbQuality'))
const LhbBacktest = lazy(() => import('./pages/v2/LhbBacktest'))

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
              <Route path="/v2/lhb/radar" element={<LhbRadar />} />
              <Route path="/v2/lhb/profile" element={<LhbProfile />} />
              <Route path="/v2/lhb/timeline" element={<LhbStockTimeline />} />
              <Route path="/v2/lhb/network" element={<LhbNetwork />} />
              <Route path="/v2/lhb/quality" element={<LhbQuality />} />
              <Route path="/v2/lhb/backtest" element={<LhbBacktest />} />
              <Route path="*" element={<Overview />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </div>
  )
}
