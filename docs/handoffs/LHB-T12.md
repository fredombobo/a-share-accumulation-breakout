# LHB-T12 Handoff — Overlay、就绪门禁与验收报告

> 工程 PASS ≠ 研究 edge PASS。永不打开 LIVE_TRADING_ENABLED。

## 1. 身份

- 任务 ID：T12
- 前置：T01–T11 工程交付
- 时间：2026-08-29

## 2. 范围

- `ab_screener/application/lhb_overlay.py`（默认关闭）
- `ab_screener/application/lhb_readiness.py`
- `docs/ACCEPTANCE-LHB-V1.md`
- `tests/test_lhb_overlay_boundaries.py`、`tests/test_lhb_readiness.py`

未改 `configs/platform_v2.yaml` 生产旗标。

## 3. 设计

- overlay 关闭：候选列表深拷贝等价，无 `lhb_research`
- overlay 打开且 RESEARCH_BLOCKED：只追加解释字段，score/rank/position/pool 不变，不生成订单
- 最低 shadow：30 个成熟独立信号且 3 个月；建议 6–12 个月 / 100 事件后再讨论晋升
- 任一 OOS / 回撤 / 容量 / 反过拟合 / shadow 失败 → RESEARCH_BLOCKED

## 4. 测试

overlay / readiness 测试含于 141 passed。

## 5. 回滚

删除 overlay/readiness 模块；验收文档可留作否决记录。

## 6. 自评

工程交付完成。研究状态 **RESEARCH_BLOCKED**。清单第 6 节「工程通过」**未写**（全量 pytest 既有失败 + 无真实副本 5 日 ingest soak + shadow 未成熟）。
