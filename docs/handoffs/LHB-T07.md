# LHB-T07 Handoff — 席位 / actor / 股票画像

> 研究 overlay。不产生订单，不打开生产旗标。

## 1. 身份

- 任务 ID：T07
- 前置：T06
- 时间：2026-08-29
- 交付 commit：无（未要求提交）

## 2. 范围

新增/完善：

- `ab_screener/application/lhb_profiles.py`
- `ab_screener/data/lhb_repository.py`（画像快照写入 `lhb_feature_snapshot`）
- `tests/test_lhb_profiles.py`

未改 A 池、未写生产库、未打开旗标。

## 3. 设计

- Wilson 区间 + Jeffreys / Laplace 收缩；n=3 全胜不得展示未收缩 100%。
- 席位 / actor / 股票 / 板块口径隔离。
- 下一开盘入场；一字涨停 `UNFILLABLE`，停牌 `SUSPENDED`。
- 金额可下钻复算（fen 合计）。

## 4. 测试

`tests/test_lhb_profiles.py` 含于 LHB 全列表 141 passed。

## 5. 回滚

删除上述模块与测试；画像快照表属于 T01 schema，勿删迁移。

## 6. 自评

- 建议判定：工程可验收
- 未宣布 edge / 可跟单
