# 阶段3-7 验收报告：纸面交易核心链路（订单/撮合/日结/前端/门禁）

日期：2026-08-08
状态：✅ 全部通过（全量测试 134/134）

## 阶段3：草稿、确认与预交易风控（`paper_trading/orders.py`）

| 验收项 | 实测 |
|---|---|
| A池正常买入草稿 | ✅ 数量=权益×min(建议,10%) 整手向下取整 |
| 持仓卖出草稿 | ✅ 可卖份额检查，禁止卖空/超卖 |
| B池/防守期买入 | ✅ MARKET_DEFENSE 拒 |
| 重复买入（已有活动买单） | ✅ DUPLICATE_ACTIVE_ORDER 拒 |
| 现金不足 | ✅ INSUFFICIENT_CASH 拒（订单置 REJECTED） |
| 不可卖份额（T+1） | ✅ INSUFFICIENT_SELLABLE_QUANTITY 拒 |
| 过期行情 | ✅ STALE_QUOTE 拒 |
| 取消订单释放预留 | ✅ reserve_fen 归零 |
| 状态机 | ✅ DRAFT→CONFIRMED→CANCELLED/REJECTED，终态拒确认 |
| 服务重启状态不丢失 | ✅ SQLite 持久化 |

**测试**：`tests/test_orders.py` 10 项。

## 阶段4：仿真撮合与会计处理（`paper_trading/engine.py`）

| 验收项 | 实测 |
|---|---|
| 次日正常开盘全额成交 | ✅ 开盘价±滑点，限高低区间 |
| 一字涨停买单/一字跌停卖单 | ✅ 零成交 |
| 停牌（无当日行情） | ✅ 零成交，订单保留顺延 |
| 小成交量（5%<一手） | ✅ INSUFFICIENT_LIQUIDITY 零成交 |
| 部分成交余量过期 | ✅ PARTIALLY_FILLED_EXPIRED |
| 同一交易日循环两次不重复成交 | ✅ 第二次 0 成交 |
| 现金变化逐项复算误差零分 | ✅ test_fill_cash_reconciles_exact |
| FIFO 批次核销 + 已实现损益 | ✅ 卖出核销 + 审计 |
| 禁止负现金/超卖 | ✅ 抛 DomainError |

**测试**：`tests/test_engine.py` 8 项。

## 阶段5：日结、估值和对账（`paper_trading/settlement.py` + 调度器）

| 验收项 | 实测 |
|---|---|
| 收盘估值（现金+市值+总资产+未实现损益） | ✅ 批量查询优化 |
| 正常日结对账差异为零 | ✅ R1-R7 全通过 |
| 一分钱现金差异被捕获 | ✅ CASH_FLOW_SUM_MISMATCH |
| 超卖记录被捕获 | ✅ OVERSOLD |
| 阻断差异存在日结不完成 | ✅ snapshot_ok=False |
| 自动日终调度 | ✅ 16:15 后每分钟轮询，幂等，重启补跑 |
| 100 持仓摘要 <500ms | ✅ 批量 SQL 优化（IN + GROUP BY） |

**测试**：`tests/test_settlement.py` 6 项。

## 阶段6：前端纸面交易工作台（`pages/PaperTrading.tsx`）

- `/paper` 路由 + 侧边栏「📋 纸面交易」+ Topbar 标题
- 六块：账户摘要 / 持仓 / 订单（草稿确认取消）/ 成交 / 旧持仓导入（预览-确认）/ 对账
- 显著 banner「纸面仿真交易—仅模拟，不会向券商下单 · LIVE_TRADING_DISABLED」
- 确认/取消/导入均有二次确认 + 成功/失败反馈
- 键盘可操作（原生 button/input + aria-label）
- `api.client.ts` 新增 16 个 paper API 方法 + 6 个类型

**验收**：桌面/窄屏可用（flex-wrap + auto-fit grid）；TS 编译零错误；vite build 成功。

## 阶段7：独立真实数据门禁（`paper_trading/real_data_gate.py` + `__main__.py`）

```
python -m paper_trading.real_data_gate --days 730 --report runtime/gates/
```

- 不调用 Web API / 扫描缓存，直接数据适配器验证本地库 vs Tushare
- 无 TUSHARE_TOKEN → status=NOT_RUN + 退出码 2，绝不视为通过
- Token 无效 → ERROR + 退出码 1（实测 token 过期场景正确失败）
- 检查项：日历覆盖≥730日 / 本地对齐 / 活跃标的覆盖≥98% / 持仓订单A池覆盖100% /
  主键无重复 / OHLC 有效 / 抽样比对（20标的×5日期）/ 元数据完整
- 报告含 code_version / config_hash / db_fingerprint / date_range / samples / sha256，**不含 Token**
- 日常运行只做轻量新鲜度检查（gates/status 端点）

## 综合数据

- 全量测试：**134 passed**（阶段0:81 + 阶段1:23 + 阶段2:8 + 阶段3:10 + 阶段4:8 + 阶段5:6 - 部分合并）
- ruff：paper_trading/ + backend 全绿（pyproject.toml 选择性忽略历史风格项）
- mypy：paper_trading 13 文件 + backend 全绿（--follow-imports=skip）
- 前端：tsc 零错误 + vite build 成功
- 后端：/paper SPA + 全部 paper API 200，纸面仿真标识正常
