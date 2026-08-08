import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import Sidebar from './layout/Sidebar'
import Topbar from './layout/Topbar'

const Overview = lazy(() => import('./pages/Overview'))
const StockDetail = lazy(() => import('./pages/StockDetail'))
const StrategyLab = lazy(() => import('./pages/StrategyLab'))
const PaperTrading = lazy(() => import('./pages/PaperTrading'))

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
              <Route path="/paper" element={<PaperTrading />} />
              <Route path="*" element={<Overview />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </div>
  )
}
