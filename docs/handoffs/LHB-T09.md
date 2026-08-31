# LHB-T09 Handoff — 事件研究、回测与反过拟合门禁

> 研究 overlay。未过门禁不得称 edge。

## 1. 身份

- 任务 ID：T09
- 前置：T08
- 时间：2026-08-29

## 2. 范围

- `ab_screener/research/lhb_event_study.py`
- `ab_screener/research/lhb_backtest.py`
- `ab_screener/research/lhb_validation.py`
- `tests/test_lhb_event_study.py`
- `tests/test_lhb_backtest_no_lookahead.py`

## 3. 设计

- 只用 `available_at <= as_of` 的事实生成历史信号；不改写披露时间
- 涨停样本不按开盘成交
- 匹配对照与未匹配原始收益并列
- 试验全登记，失败也保留
- `can_claim_edge` 仅当 verdict=PASS

## 4. 测试

含于 LHB 全列表 141 passed。当前验证路径返回 INSUFFICIENT/FAIL，不声称 PASS。

## 5. 回滚

删除 research/lhb_* 与对应测试。

## 6. 自评

工程可验收。研究状态 RESEARCH_BLOCKED。
