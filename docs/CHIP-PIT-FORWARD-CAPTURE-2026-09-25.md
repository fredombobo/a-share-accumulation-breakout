# 筹码前向 PIT 捕获（2026-09-25）

> 对应 `PHASE0-2-PROGRESS-2026-09-15.md` 第 3 节「下一步」第 1 条：真 PIT 筹码。
> 本文件只记录工程实现，不构成任何收益结论；评分仍为 2/10。

## 为什么要做

Phase 2-A 预登记（`PREREGISTRATION-CHIP-OVERHANG-2026-09-15.md`）要求筹码值
`available_at ≤ 次一交易日 09:15`。库内 `cyq_history` 是 2026-08-18 / 08-22 批量入库，
`available_at` 是入库时刻，严格口径下可用事件为 **0**（`INSUFFICIENT_PIT`）。

历史可用时点不能倒填，唯一合规的做法是：**从现在起每个交易日收盘后抓取当日快照，
把真实抓取完成时刻记为 `available_at`**。在此之前，这条实现一直没有接进日用链路。

## 实现

| 文件 | 作用 |
|---|---|
| `ab_screener/data/chip_pit_capture.py` | 捕获 + 严格 PIT 就绪统计 |
| `scripts/capture_chip_pit.py` | CLI：计划 / `--apply` / `--readiness` |
| `daily_run.ps1` 第 2 步 | 行情同步后、扫描前自动执行；`-SkipChipCapture` 可跳过 |
| `tests/test_chip_pit_capture.py` | 19 项离线测试 |

规则：

- **只前向**：每次只抓一个交易日（默认 `daily` 最新交易日），不补历史。
- **可用时点保守**：取抓取完成之后的时刻，只会比真实可用更晚，不会更早。
- **append-only**：与同一 (ts_code, trade_date) 最新 revision 的 content_hash 比较，
  只追加新增或变化的行；同内容重跑为 NOOP，保留最早的可用时点。
- **分页防截断**：`offset/limit` 翻页。网关若忽略 offset（翻页返回已见过的股票）或
  连续满页超过上限，整批拒绝写入，不静默截断。
- **覆盖率**：分母为同日有行情的沪深股票；低于 95% 记 `PARTIAL`，已到的行照样写入，
  稍后重跑只补缺失行（这些行的可用时点是补抓时刻）。北交所行单独计数。
- **盘中拒绝**：目标交易日 15:00 之前拒绝抓取。
- **不迁移**：缺 `cyq_history` / `raw_ingest_manifests` / `daily` 时拒绝执行。
  写入方式与 `sync_daily.py` 对 `daily_history` 追加 revision 相同，不是 schema 变更。
- 不触碰 `pt_*` 纸面账本、不改每日选股参数、不改变任何研究结论。

## 用法

```powershell
.\.venv312\Scripts\python.exe scripts\capture_chip_pit.py              # 只出计划，不访问网络
.\.venv312\Scripts\python.exe scripts\capture_chip_pit.py --apply      # 抓取并追加写入
.\.venv312\Scripts\python.exe scripts\capture_chip_pit.py --readiness  # 只读：严格 PIT 可用交易日
```

退出码：0 = PLANNED / COMPLETED / NOOP；3 = PARTIAL / EMPTY；2 = 拒绝执行。
日用链路里非 0 只警告，不影响选股和 `DAILY_COMPLETE`。

若 18:30 日跑时供应商尚未发布（EMPTY），当晚到次日 09:15 前手动重跑 `--apply`
仍然满足预登记口径。

## 验收状态

- 离线：19 项新测试 + 全量离线套件通过（本环境无 PowerShell 的 2 项备份合约测试除外，CI 有）。
- **未做真实数据验收**：开发环境没有生产库和 Token。首次在本机运行需确认：
  1. 镜像网关支持 `cyq_perf` 的 `offset/limit`（不支持会明确拒绝，不会写入截断数据）；
  2. 单日行数与覆盖率合理（`--apply` 输出里的 `fetched_rows / coverage / pages`）；
  3. `--readiness` 的 `strict_usable_dates` 从 1 开始逐日增长。

## 它解锁什么、不解锁什么

- 解锁：从首个捕获日起，筹码维度的事件可以按预登记 PIT 口径使用。
- 不解锁：已有探索结论（`winner_rate` 反向）仍只是探索；H2 需要按
  `PREREGISTRATION-TEMPLATE-V0.md` **先冻结**假设、最小样本量与阈值，再用捕获期内
  未看过的事件检验。样本需要按月积累，在预登记的最小样本达到前不得查看结果。
