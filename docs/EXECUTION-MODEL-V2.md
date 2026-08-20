# EXECUTION-MODEL-V2 — 唯一执行领域核心（P2.1）

> 契约版本：v2 · 2026-08-18 · `EXECUTION_MODEL_VERSION = v2.1.0`

## 1. 范围与原则

统一撮合、费用、交易规则与账本精度。所有金额一律**整数「分」（fen）**，
价格用**整数微元（micro = 元 × 1_000_000）**；浮点账务输入直接拒绝（fail-closed）。

- 未知费用版本拒绝（`FeeParams.version` ≠ 当前版本 → `MoneyError`）。
- 负现金、超卖、重复成交（缺 `input_hash`）拒绝。
- 规则可逐项复算（tick/滑点/佣金最低值/税费/FIFO/T+1），误差为零分。

## 2. 模块

| 模块 | 职责 |
|------|------|
| `models.py` | `Quote`（微元 K 线）、`FillV2`、`FeeBreakdown`、`MoneyError`、`require_int_fen/micro` |
| `fees.py` | 整数分费用：佣金 max(最低, 名义×万五)、卖出印花千一、其他费万一、滑点拆解 |
| `market_rules.py` | 可交易性（NO_QUOTE/NO_VOLUME/一字涨停买/一字跌停卖）、tick、滑点钳制、参与率 |
| `fill_model.py` | `compute_fill`：参与率限量 → 资金/持仓约束 → 费用拆解 → 现金变动 |
| `settlement_rules.py` | 现金预算整手、可卖数量、FIFO 批次消耗（已实现盈亏分）、T+1 可卖日 |
| `dual_run.py` | 与旧核心（`ab_screener.domain.costs`）对比；不一致不切换写路径 |

## 3. 撮合语义（v2）

1. **可交易门**：无 open / 无量 / 一字涨停买 / 一字跌停卖 → 零成交（带 reason）。
2. **参考价** = 当日 open（入场时序与 ENTRY V1 一致：突破日下一交易日开盘）。
3. **滑点**：买 `×(1+slippage_bps/10000)`、卖反向；钳制在当日 `[low, high]`；按 tick 0.01 元取整（round-half-up）。
4. **参与率**：`max_qty = floor(vol × participation_bps / 10000)`，默认 500 bps（5%），再按交易单位 100 向下取整。
5. **资金约束**（BUY）：`cash_available_fen` 预算内含费可买整手；不足降档；仍不足 → `INSUFFICIENT_CASH` 零成交。
6. **持仓约束**（SELL）：`position_qty` 向下取整到整手；超卖拒绝。
7. **费用**：佣金 = `max(commission_min_fen, round_half_up(notional × bps / 10000))`；卖出印花税；其他费；滑点拆解。
8. **现金变动**：BUY `-notional - 佣金 - 税 - 其他费`；SELL `+notional - 佣金 - 税 - 其他费`（整数分）。
9. **防重复成交**：`FillRequest.input_hash` 必填（模型层强制；持久层去重在 P2.3 血缘固化）。

## 4. FIFO 与 T+1

- `consume_fifo_lots(lots, qty, sell_price_micro)`：按买入批次先后消耗；
  已实现盈亏（分）= Σ(卖出价 - 批次成本价) × 数量（1 分 = 10_000 微元）。
- `next_sellable_date(trade_date, open_dates)`：T+1 下一开市日；无 → `None`（fail-closed）。

## 5. 与旧核心的关系（dual-run）

- 旧核心（`costs.py`，float 元口径）保持冻结为 `legacy-v1`。
- `costs.dual_run_observer()` 返回 `v2_ready_for_write_path`；当前为 `False`：
  纸面/研究写路径切换需等 P2.2 可成交语义与 parity 测试完成（验收：不一致不切换）。

## 6. 验收证据

- `tests/test_execution_core_v2.py`（撮合/零成交/参与率/tick/FIFO/T+1/拒绝语义）
- `tests/test_execution_money_exactness.py`（整数分逐项复算 + dual-run 对比 + 费用版本拒绝）
- 17 用例全绿；全量离线门禁绿后提交。

## 7. 已知边界（诚实声明）

- 本包是纯领域核心，尚未接管纸面/研究写路径（P2.2 进行中）。
- `_largest_lot_within` 为保守逐档搜索，仅用于买入降档。
- 滑点拆解按「成交价 - 参考价」的绝对影响额计算，与旧核心 `slip_cost` 口径一致。
