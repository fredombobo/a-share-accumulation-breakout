# LHB-T03 Handoff — 历史回填、跨源对账和数据质量门禁

## 1. 身份

- 任务 ID：T03
- 基线 commit：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`
- 交付 commit：无
- 时间：2026-08-29

## 2. 范围

新增/修改：

- `ab_screener/application/pit_backfill.py`（`top_inst`/`hm_list`、空分区 fail-closed、生产库拒绝）
- `ab_screener/application/lhb_reconcile.py`
- `ab_screener/data/pit_writer.py` / `pit_repository.py`（合并 LHB PIT 表，不改已发布 intent）
- `scripts/backfill_pit_v2.py`（拒绝 `runtime/stock_data.db`）
- `tests/test_lhb_backfill_resume.py`、`tests/test_lhb_reconciliation.py`

## 3. 设计

- 回填只接受绝对路径副本；生产库路径直接拒绝。
- `top_list`/`top_inst` 空结果不标 `done`，记 `EMPTY_WITHOUT_PUBLISHED_FLAG`。
- 覆盖率用交易日历开市日，周末不进缺日。
- 对账写入 `lhb_reconciliation`，append-only，保留双方原值。
- 门禁失败返回 `INSUFFICIENT`，`allows_confirmed_signal=false`。
- 20 日 × 20 席位追溯 `raw_ingest_manifests` 在离线 fixture 完成。

未改 feature flag，未写生产库。

## 4. 测试

```powershell
.\.venv312\Scripts\python.exe -m pytest tests\test_lhb_backfill_resume.py tests\test_lhb_reconciliation.py tests\test_pit_backfill_resume.py -q
# 与 PIT 回归合计 32 passed（该批）
```

真实 Token 仅用于 T02 smoke，T03 回填测试全部 fake。

## 5. 回滚

还原 pit_writer / pit_backfill / backfill_pit_v2；删除 lhb_reconcile 与新测试。

## 6. 管理者复验

- 最终判定：**返工复验通过**。
- 已关闭：最近 5 个龙虎榜分区重验可发现 T+1 修订；对账使用稳定业务键并将金额变化追加为新 revision；席位、原因和 side 写入 locator；双方单位必须显式传入。
- 下一步：T06 可继续；完整证据见 `docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`。
