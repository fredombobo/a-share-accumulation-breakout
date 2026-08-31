# LHB-T11 Handoff — 盘后 DAG 与告警 ACK

> 独立 LHB DAG，不改冻结的 `DAG_STEPS`。调度旗标保持 false。

## 1. 身份

- 任务 ID：T11
- 前置：T08
- 时间：2026-08-29

## 2. 范围

- `ab_screener/application/lhb_daily.py`（mode=`LHB_EOD`）
- `ab_screener/operations/lhb_alerts.py`
- 新迁移 `v2:lhb_ops`（仅 `lhb_alert_delivery`，不改 T01/operations checksum）
- `tests/test_lhb_daily_dag.py`、`tests/test_lhb_alerts.py`

## 3. 设计

- 幂等键 `trade_date + step + scope + input_hash`；重跑不重复执行
- `dag_leases` 竞争同一 `lhb:{date}` 只有一个 holder
- FETCH_FAILED/DEGRADED 阻断 confirmed，仍发 DATA_QUALITY 告警
- 状态机 CREATED / SENT / ACKED / FAILED / DEAD_LETTER；SENT 无 ACK ≠ 已送达
- 历史重放 `historical_replay=True` 不调用真实通知
- 合成 5 日 soak：temp DB 上 5 个交易日 COMPLETE，dag_runs=5

## 4. 测试

含于 LHB 全列表 141 passed。

未做：维护副本上的真实 ingest 5 日 soak（无生产库写入）。

## 5. 回滚

删除 lhb_daily / lhb_alerts / `v2:lhb_ops`（需同步改 checksum 排除规则）。勿回改 `DAG_STEPS`。

## 6. 自评

工程可验收。`DAILY_SCHEDULER_ENABLED` 仍为 false。
