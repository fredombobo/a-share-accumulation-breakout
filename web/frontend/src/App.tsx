import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import Sidebar from './layout/Sidebar'
import Topbar from './layout/Topbar'

const Overview = lazy(() => import('./pages/Overview'))
const StockDetail = lazy(() => import('./pages/StockDetail'))
const ProfessionalBacktest = lazy(() => import('./pages/ProfessionalBacktest'))

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
              <Route path="/backtest" element={<ProfessionalBacktest />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </div>
  )
}
