# LHB T02–T05 审查返工

> 二次返工：真实抽样确认 `side` 仅 `'0'`/`'1'`，95 组同席位同原因多行，88 组双榜。
> PowerShell 验收请用 `scripts/run_lhb_pytest.ps1`，不要用 `test_lhb_*.py` glob。

> 针对主验收 8 项否决。T01 契约未改 migration checksum。

## 修复对照

| # | 问题 | 修复 |
|---|------|------|
| 1 | `top_inst.side` 为 `'0'`/`'1'` 不入 BUY/SELL | `normalize_top_inst_side`：0→BUY、1→SELL；prepare 与 `df_to_pit_rows` 都映射 |
| 2 | 双榜两行金额相加翻倍 | `_merge_seat_amounts` 按 side 取 max，禁止 `+=` |
| 3 | 不同原因/窗口席位混用 | `_inst_rows_for_window`：D3 只用三日原因明细；缺原因只归 D1 |
| 4 | 对账 dict 覆盖双榜；默认双方万元 | 索引键含 `side` 且值为 list；`left_unit`/`right_unit` 显式换算 |
| 5 | as-of 只滤别名 | `lookup_as_of` 同时过滤 `seat_master` 与 `seat_actor_hypothesis` 有效期 |
| 6 | 第二个别名主键冲突 | `save_hypothesis` 已有 master 则只追加 alias |
| 7 | 对账 revision=1 重跑失败 | 相同 content_hash 跳过；变化才 +revision |
| 8 | ingest 写 `lhb_ingest_manifests`，追溯只查 `raw_ingest_manifests` | ingest 同时写两条；`trace_to_manifest`/`manifest_exists` 两表合一 |

## 测试

```powershell
.\.venv312\Scripts\python.exe -m pytest tests\test_lhb_contracts.py tests\test_lhb_source_adapters.py tests\test_lhb_normalization.py tests\test_lhb_reconciliation.py tests\test_lhb_backfill_resume.py tests\test_seat_identity.py tests\test_pit_writer.py tests\test_pit_backfill_resume.py -q
```

二次返工后定向（明确文件列表，含 `test_lhb_real_shape.py`）：69 passed。
未写生产库，未开交易旗标。未进入 T06。
既有全量收集问题 `v2:corporate_action_pit` 不在本仓库 LHB 改动范围内。
严格反过拟合 / 持仓 `stale_local_cache` 未在本波处理。
