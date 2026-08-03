# accumulation_breakout — Grok 工作约定

> 从 Hermes 迁移到 Grok 的 A 股「横盘吸筹→启动」选股系统。  
> 根目录：`E:\CODEX\Stock_selection\accumulation_breakout`

## 系统概览

三层过滤：技术形态（箱体+放量突破）→ 资金流 → 基本面打分。

| 模块 | 文件 | 职责 |
|------|------|------|
| 参数 | `config.py` | 箱体/突破/基本面阈值 |
| 信号 | `signals.py` | 横盘吸筹 + 启动检测 |
| 打分 | `scoring.py` | 资金流 + 基本面 + 综合分 |
| 数据 | `data_fetch.py` / `local_store.py` / `sync_daily.py` | SQLite 增量 + Tushare 直连 |
| 扫描 | `run_screener.py` | CLI 全市场扫描 → xlsx/md/charts |
| Web | `web/backend_app.py` + `web/frontend` | FastAPI 8000 + React 3001 |
| 客户端 | `tushare_http.py` | curl_cffi 直连（TLS 指纹） |

## 运行前环境

**小白入口（优先）：** 双击 `一键启动.bat` → 浏览器 `http://127.0.0.1:8000/` → 点「扫描」  
停止：双击 `停止.bat`。详见 `docs/小白使用手册.md`。

```powershell
# 清除 Hermes/代理污染
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
$env:http_proxy=$env:https_proxy=$env:all_proxy=$null

# Token：.env 或环境变量 TUSHARE_TOKEN
cd E:\CODEX\Stock_selection\accumulation_breakout
C:\Python314\python.exe easy_start.py
C:\Python314\python.exe sync_daily.py
C:\Python314\python.exe run_screener.py --top 15 --days 160 --workers 0
C:\Python314\python.exe test_signals.py
```

Web（进阶）：单端口已托管 `web/frontend/dist`，一般只需 `backend_app.py` :8000。
开发热更新才需要 `npm run dev` :3001。

## 硬约束（踩坑后写死）

1. **Tushare 必须 curl_cffi**，禁止 requests 直连 `a.sszhixia.cn`（TLS 指纹 → 10054）。
2. **禁止全市场 fina_indicator 循环**；只对候选股 `sync_fina_for_codes`。
3. **SQLite 每操作新连接**；upsert 用 `ON CONFLICT DO UPDATE`，禁止 `INSERT OR REPLACE`（会静默 NULL 列）。
4. **sync 按交易日历 diff 补洞**，不要只看 MAX(trade_date)。
5. **推荐前必须核对 as_of / 数据新鲜度**；过期结果不可直接交付。
6. 资金流单位是**万元**；前端展示换算时勿再 /100。
7. 突破日格式：后端可用 `YYYY-MM-DD`，ECharts 轴是 `YYYYMMDD`，比对前 normalize。

## 数据现状（迁移时快照）

- DB：`runtime/stock_data.db`（~1GB，WAL）
- 最新交易日：以 `store.max_trade_date('daily')` 为准（迁移审计时为 `20260731`）
- scan_result：Top 20 已有（as_of 同最新扫描日）

## 输出规则（胜率优先 · 2026-08-03）

- **A 池可交易**：仅 `strict`，默认 Top **15**（`TOP_N=15`）；含止损/目标/仓位
- **B 池观察**：`relaxed` / `theme_fill`，**禁止与 A 混排**
- 主题：**软加分偏好**，不再硬凑每板块 5 只进 A
- 横盘 **1~6 个月**（20~125 交易日）；`HORIZON_DAYS=160`
- 箱体专业判定：稳健振幅（去影线极值）+ 支撑/压力各≥2 次触及 + 中部占用 + 摆动 + 前后漂移/R² 拒通道；箱体右端锚定突破日前
- 环境过滤：防守期 A 池清空（禁止新开仓）
- 数据过期：`data_freshness` 按**交易日**滞后（排除周末/节假日；16:00前今日数据未齐则期望为上一交易日）
- CLI：`python run_screener.py --top 15 --days 160 --workers 0`
- 多核：`parallel_scan.py`（ProcessPool，默认 `SCAN_WORKERS=0`→cpu-1）；strict + relaxed 全市场并行，不再截断 800
- 回测：`python backtest_signals.py`
- 持仓：`runtime/portfolio.json` + `GET/POST /api/portfolio` + UI `/portfolio`
- 一键 UI：`.\start_ui.ps1` / `.\stop_ui.ps1`
- 预筛加速：`prefilter_volume_parallel`（量能/近高点，多进程）
- 指数环境：优先 `000300.SH`（`ensure_index_daily`），否则市场中位涨跌

## 优化 backlog（下一步优先）

1. **数据时效**：补齐最新交易日；扫描 as_of 与真实最新对齐。
2. **自包含依赖**：确认 `.env` 中 `TUSHARE_TOKEN`。
3. **测试**：`test_signals.py` + `test_sector_themes.py`；补 scoring / API。
4. **前端**：展示主题标签与配额达成徽章。
5. **参数与过拟合**：放宽层单独回测验证。

## 相关 Grok skill

`~/.grok/skills/a-share-accumulation-breakout/SKILL.md`
