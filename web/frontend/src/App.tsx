import { Route, Routes } from 'react-router'
import Sidebar from './layout/Sidebar'
import Topbar from './layout/Topbar'
import Overview from './pages/Overview'
import StockDetail from './pages/StockDetail'
import Portfolio from './pages/Portfolio'

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <div className="main">
        <Topbar />
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/stock/:tsCode" element={<StockDetail />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="*" element={<Overview />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
