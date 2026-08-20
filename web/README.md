# 横盘吸筹→启动 选股终端（Web UI）

## 架构

```
accumulation_breakout/
├── config.py / signals.py / scoring.py / charting.py   # 选股引擎（Python）
├── run_screener.py        # CLI 全市场扫描 → out/ (xlsx+md+charts)
├── make_report.py         # Markdown 报告
└── web/
    ├── backend_app.py     # FastAPI 后端（单端口 8001，托管 frontend/dist）
    └── frontend/          # React 19 + Vite + ECharts
        ├── src/theme/     # 深浅主题（ThemeContext + useChartColors）
        ├── src/pages/     # Overview（总览网格）/ StockDetail（个股详情）
        └── vite.config.ts # dev 模式 /api 代理 → 8001（默认不必要）
```

## 启动

```powershell
# 唯一入口（单端口 8001，后端自带前端，小白无需 Node）
cd E:\CODEX\Stock_selection\accumulation_breakout
python bootstrap.py --yes        # 或双击 一键启动.bat / python easy_start.py

# 开发前端热更新（可选，需先 npm install 一次；:3001 代理到 :8001）
cd E:\CODEX\Stock_selection\accumulation_breakout\web\frontend
npm run dev
```

浏览器打开 http://127.0.0.1:8001/ （dev 模式为 http://localhost:3001）

> 端口 8000 固定留给其它应用（AETF Alpha），请勿在本项目使用。

## 功能

- **总览页** `/`：指标卡（交易日/数量/平均分/资金流）+ 20 张股票卡片网格
  - 每卡：迷你K线图（含箱体上下沿虚线 + 突破图钉）、价/行业/市值/量比/主力净流入、入选理由
  - 顶部可调 Top N / 回看天数 + 运行扫描
- **个股详情** `/stock/:tsCode`：
  - K线大图：红涨绿跌蜡烛 + 箱体上下沿虚线 + 突破日图钉 + MA5/MA20 + 成交量副图 + dataZoom
  - 信号解读：箱体天数/振幅/区间/突破日/量比/涨幅/缩量系数/MA
  - 资金流：近5日主力净流入/占比/强度分（后端实时补拉）
  - 财务摘要：PE/PB/市值/换手率/量比
- **策略实验室** `/lab`：参数研究（IS/OOS/WF/双基线/反过拟合门禁），非下单入口
- **纸面交易** `/paper`：订单风控/仿真撮合/日结对账（LIVE_TRADING 恒关闭）
- **主题**：右上角 ☀/◐ 切换深/浅色，localStorage 记忆

## 后端 API

- GET /api/overview          → 总览（含每只K线+箱体）
- GET /api/stock/{ts_code}   → 个股详情（K线/信号/资金流/基本面）
- POST /api/scan             → 触发重新扫描 {top, days, force}
- GET /api/health
- 安全（2026-08-16）：CORS 白名单化 + Host/Origin 本机校验；纸面导入路径限定 runtime/portfolio.json

## 数据源

Tushare 直连（统一经 `tushare_init.py` 初始化，curl_cffi 抗 TLS 指纹；Token 用 `TUSHARE_TOKEN` / `.env`，勿提交）
