# 横盘吸筹→启动 选股终端（Web UI）

## 架构

```
accumulation_breakout/
├── config.py / signals.py / scoring.py / charting.py   # 选股引擎（Python）
├── run_screener.py        # CLI 全市场扫描 → out/ (xlsx+md+charts)
├── make_report.py         # Markdown 报告
├── app.py                 # Streamlit 版（旧，简单面板）
└── web/
    ├── backend_app.py     # FastAPI 后端（端口 8000）
    └── frontend/          # React 19 + Vite + ECharts（端口 3001）
        ├── src/theme/     # 深浅主题（ThemeContext + useChartColors）
        ├── src/pages/     # Overview（总览网格）/ StockDetail（个股详情）
        └── vite.config.ts # /api 代理 → 8000
```

## 启动

```bash
# 1) 后端（8000）
cd E:\CODEX\Stock_selection\accumulation_breakout\web
C:\Python314\python.exe backend_app.py

# 2) 前端（3001，需先 npm install 一次）
cd E:\CODEX\Stock_selection\accumulation_breakout\web\frontend
npm run dev
```

浏览器打开 http://localhost:3001

## 功能

- **总览页** `/`：指标卡（交易日/数量/平均分/资金流）+ 20 张股票卡片网格
  - 每卡：迷你K线图（含箱体上下沿虚线 + 突破图钉）、价/行业/市值/量比/主力净流入、入选理由
  - 顶部可调 Top N / 回看天数 + 运行扫描
- **个股详情** `/stock/:tsCode`：
  - K线大图：红涨绿跌蜡烛 + 箱体上下沿虚线 + 突破日图钉 + MA5/MA20 + 成交量副图 + dataZoom
  - 信号解读：箱体天数/振幅/区间/突破日/量比/涨幅/缩量系数/MA
  - 资金流：近5日主力净流入/占比/强度分（后端实时补拉）
  - 财务摘要：PE/PB/市值/换手率/量比
- **主题**：右上角 ☀/◐ 切换深/浅色，localStorage 记忆

## 后端 API

- GET /api/overview          → 总览（含每只K线+箱体）
- GET /api/stock/{ts_code}   → 个股详情（K线/信号/资金流/基本面）
- POST /api/scan             → 触发重新扫描 {top, days, force}
- GET /api/health

## 数据源

Tushare HTTP 直连 http://a.sszhixia.cn/（curl_cffi，见上级目录 `tushare_http.py`；Token 用 `TUSHARE_TOKEN` / `.env`）
