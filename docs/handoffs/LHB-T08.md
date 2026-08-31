# LHB-T08 Handoff — 研究信号与硬否决

> 研究 overlay。不产生订单，不打开生产旗标。

## 1. 身份

- 任务 ID：T08
- 前置：T07
- 时间：2026-08-29

## 2. 范围

- `ab_screener/domain/lhb_signal.py`
- `ab_screener/application/lhb_signal_engine.py`
- `configs/lhb_signal_policy.yaml`（`overlay_enabled: false`）
- `tests/test_lhb_signal_engine.py`

## 3. 设计

- 状态：WATCH / CONFIRMED_FLOW / RESEARCH_ENTRY / NO_CHASE / INVALIDATED
- `earliest_executable_at` = 下一交易日 09:30+08:00，不得早于披露
- 数据不完整最多 WATCH；涨停/停牌/流动性不足 → NO_CHASE
- 快照重算分数与否决，不读当前 yaml；改阈值产生新 policy_hash

## 4. 测试

含于 LHB 全列表 141 passed。

## 5. 回滚

删除信号模块与 yaml；观察表属于 T01 schema。

## 6. 自评

工程可验收。research_only 恒 true。
