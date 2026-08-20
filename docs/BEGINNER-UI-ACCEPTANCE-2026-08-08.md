# 策略实验室与纸面交易小白化验收报告

验收日期：2026-08-08（Asia/Shanghai）  
结论：**PASS**

## 已交付

- Lab 首次默认引导模式：A/B 一句话选择、`full` 数据门禁、固定可信预设、五阶段进度、跨页/刷新恢复、三类人话结论。
- Paper 首次默认引导模式：唯一下一步、账户说明、日期选择、后端只读预览、确认、开盘撮合、结果与对账、持仓行卖出。
- 专业视图保留原参数台、报告、订单、成交、导入、门禁、对账和设置。
- 结构化 `ApiError` 保留错误码、详情、重试属性和 HTTP 状态；引导模式默认显示原因与解决办法。
- 隔离教程使用本地历史行情和固定 10 万元演示资金，不写纸面账本。
- `GUIDED_UI_ENABLED=false` 可回退到专业工作台；`LIVE_TRADING_ENABLED` 保持关闭。

## 接口验收

- `GET /api/paper/dashboard` 已返回 `guide`。
- `GET /api/paper/trading-calendar` 已进入 OpenAPI。
- `POST /api/paper/orders/review` 已进入 OpenAPI，无需 `Idempotency-Key`，返回 `persisted=false`。
- 实机教程预览：`000001.SZ`、成交日 `20260807`、决策日 `20260806`，可预览且无写入。
- 发布实例：build `7e48e4bdde69`，启动时间 `2026-08-08T21:16:54`，行情日 `20260807`，研究模式 `full`。

## 自动化证据

```text
python -m pytest -q
303 passed, 134 warnings

python -m ruff check .
All checks passed!

python -m mypy paper_trading web/backend_app.py
Success: no issues found in 17 source files

npm --prefix web/frontend run build
677 modules transformed; build PASS
```

浏览器验收：

- 固定可信预设为 grid + 600 只 + 步长 10 + 完整网格；PASS。
- Lab 跨页任务入口、失焦恢复和 FAIL 人话结论；PASS。
- Paper 历史买入只需“确认模拟订单”和“按开盘模拟成交”；PASS。
- 持仓行卖出自动带入代码和可卖上限；PASS。
- 教程只读；PASS。
- 原专业 Lab/Paper 工作台回归；PASS。
- 390px 无横向溢出；PASS。
- 浏览器控制台错误：0。

截图：`runtime/beginner-guided-live-acceptance.png`、`runtime/lab-trusted-report-acceptance.png`。

## 真实纸面账本不变证明

发布前后完全一致：

```text
cash_fen = 1000000
pt_order = 1（CANCELLED 1）
pt_fill = 0
pt_position_lot = 0
pt_cash_flow = 1
pt_audit_event = 2
pt_cycle = 6
pt_daily_snapshot = 6
pt_reconciliation = 6
pt_signal_snapshot = 30
```

教程预览、页面烟雾测试和服务重启均未改变订单、成交、现金、持仓、周期、对账或审计表。

## 非阻断提示

- Pytest 警告来自 FastAPI/Starlette 对 Python 3.14 的弃用提示和 `curl_cffi` 的 Eventlet 提示，不是测试失败。
- 前端仍有 ECharts 大包警告；图表已独立 chunk，不影响本次功能与移动端验收。
- 本系统只做纸面仿真，不包含任何券商下单能力。
