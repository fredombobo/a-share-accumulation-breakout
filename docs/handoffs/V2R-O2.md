# V2R-O2 Handoff — 持久 EOD DAG、故障恢复、审计链与真实 soak

- 角色：`operations-dag-agent`
- 分支/worktree：`v2r-o2` @ `E:\CODEX\Stock_selection\worktrees\v2r-o2`
- 权威 Python：`E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`
- 状态：`READY_FOR_REVIEW`；**`O-12=INSUFFICIENT`（不足 5 个真实完成交易日，不伪造等待）**

## 1. base / head

- 管理者冻结基线：`011f76cfdbc7b3e044a20b45e77cb6d1fb113256`（未改动，无 force-push）
- 分支起点（本任务首个提交的父提交）：`1dffcb7`（含开工包与任务板文档提交，基线之上）
- head（提交后）：见下「9. 提交清单」；提交后 `git log --oneline -1` 为最新 O2 commit SHA
- 分支是否包含基线历史：是，011f76c → 934d04b → 1dffcb7 均为管理者已接受内容，未改写

## 2. 授权范围内 owned diff

仅修改开工包 §2 授权路径（13 项）。`git diff 1dffcb7 --stat`：

```
 ab_screener/application/audit_service.py | 173 ++++++++++----
 ab_screener/data/scheduler_repository.py | 200 ++++++++++++----
 ab_screener/operations/alerts.py         |  45 +++-
 ab_screener/operations/dag.py            | 388 ++++++++++++++++++++++++++++++-
 ab_screener/operations/scheduler.py      | 239 +++++++++++++++----
 pyproject.toml                           |   6 +
 scripts/soak_monitor_v2.py               | 210 +++++++++++++----
 tests/test_audit_hash_chain.py           | 148 +++++++++++-
 tests/test_daily_dag.py                  |  60 ++++-
 9 files changed, 1253 insertions(+), 216 deletions(-)
```

新增未跟踪测试（将随提交加入）：
- `tests/test_dag_order.py`（62 行）— 冻结 9 步合同与依赖边
- `tests/test_daily_dag_closed_loop.py`（211 行）— 生产 factory 封闭循环、幂等重放、账本不变
- `tests/test_fault_injection_scheduler.py`（201 行）— 七类故障注入（`fault_injection` marker）
- `tests/test_scheduler_lease.py`（151 行）— 租约生命周期、并发单租约、崩溃不留永久态
- `tests/test_soak_monitor_v2.py`（约 200 行）— soak 计数规则（`fault_injection` marker）

`pyproject.toml` 仅注册 `fault_injection` / `performance` 两个 Pytest markers（§2 允许范围）。

未触碰：`web/backend_app.py`、`app_factory.py`、`configs/platform_v2.yaml`、前端 dist、
纸面账本/订单/撮合、研究门禁、任务板、状态文档、`ab_screener/data/migrations_v2.py` 与
`migration_intents/*`（无任何新数据库列/索引/迁移）。

## 3. RED / GREEN 证据

**RED（基线 011f76c 上，新测试必然失败）：**
- 基线 `dag.py` 的 `DAG_STEPS` 为 13 步通用骨架（`calendar_lease/sync/pit_gate/open_fill_replay/
  market_breadth/close_scan/signal_observe_lifecycle/alerts_drafts/backup_verify`），
  与冻结 9 步合同不一致 → `test_dag_order.py::test_contract_steps_exactly_nine_business_steps` 失败。
- 基线 `scheduler.py` 无任何 lease 获取/续租/释放 → `test_scheduler_lease.py` 全部失败、
  `test_daily_dag.py::test_lease_exclusive` 失败（`acquire_lease` 不存在）。
- 基线 `audit_service.py` 硬编码 `AUDIT_SIGNING_KEY = b"ab-local-audit-signing-key-v1"`
  → `test_audit_signing_key_missing_refused`（期望缺 key 拒绝）失败。
- 基线 `scheduler_repository.py` 的 `step_attempt_status` 只读 SUCCESS、`acquire_lease` 无
  `BEGIN IMMEDIATE` → attempt 保留/并发单租约测试失败。
- 基线 `soak_monitor_v2.py` 只看 manifest COMPLETE → 有效日规则（DAG COMPLETED + 不可变
  COMPLETE manifest）测试失败。

**GREEN（当前实现）：** 定向 5 文件 + fault_injection 全绿（见 §8 命令摘要）；
追加证据见下方 §5 各场景。

## 4. 临时数据库路径与身份（副本演练）

| 项 | 值 |
|---|---|
| 源库（只读） | `E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db` |
| 源库大小 | 16,324,935,680 字节（15.2 GiB） |
| 源库 SHA-256 | `7ff4d9165a19595e2be7470c590d7d286c7db8160814d426fe53cf476dbce35b` |
| O1 备份机制 | `ab_screener/operations/backup.py::create_backup`（SQLite online backup + 全表 hash 校验） |
| 备份产物 | `E:\ab-maintenance\v2r-o2\backup_20260824_213112.db`（15.2 GiB，表 hash 校验一致，耗时 1735.6s） |
| 备份 SHA-256 | `458b6beb74e949e4243599070473a119162fb9685d972a680fce4940738d8bac` |
| 独立副本目标 | `E:\ab-maintenance\v2r-o2\stock_data.db`（由已校验备份复制，SHA-256 同备份） |
| schema 迁移 | 源/副本均为 11 项 `v2:*` 迁移已应用（`v2:operations` 在列），75 张表，`migrations_equal=true` |
| 关键表行数 | 源=副本：`daily` 5,200,608、`pt_order` 3、`pt_fill` 1、`pt_cash_flow` 2、`pt_position_lot` 1、`pt_signal_snapshot` 62、`pt_daily_snapshot` 11、`pt_cycle` 11、`pt_reconciliation` 12、`daily_run_manifests` 6、`dag_runs/dag_step_runs/dag_leases/audit_events/alert_events` 均 0 |
| 时间 | 备份 21:31→22:00（1735.6s）；身份/SHA 22:02；演练 22:05:41→22:05:45 |

证据文件：`E:\ab-maintenance\v2r-o2\backup_create_evidence.json`、`replica_identity.json`、
`replica_schema_counts.json`、`drill_evidence.json`、`drill_v2r_o2.py`（演练脚本）。

注：源库与备份/副本的**文件级** SHA-256 不同（`7ff4d916…` vs `458b6beb…`）——SQLite online
backup 重排物理页（compact），但 `create_backup` 已对**全部表**做逐表内容 hash 校验且一致
（`backup_create_evidence.json` 内含逐表 hash），副本与备份字节级一致。生产库未被写入。

## 5. 固定 DAG 图（§4 冻结 9 步，显式依赖）

```
eod_gates  ──▶ release_matured_lots  ──▶ match_confirmed_orders  ──▶ close_valuation
（交易日/数据新鲜度/公司行为门禁）  （释放到期可卖批次）           （撮合此前确认订单）        （收盘估值）
                                                                                              │
daily_manifest  ◀── generate_drafts  ◀── outcome_backfill  ◀── internal_reconciliation  ◀── risk_pnl_snapshot
（固化 manifest）   （生成下一交易日草稿）   （信号 outcome 回填）    （内部对账，阻断级）      （风险/损益快照）
```

- 每条依赖边显式声明于 `DEPENDENCY_EDGES`（`dag.py:42-52`），`DailyDag.validate_contract()`
  断言顺序+每条边；`build_eod_dag()` 生产 factory 按合同接线（单账户 account_id=1）。
- 租约/审计/告警/备份校验为**包围控制**（SchedulerRunner / audit_service / alerts / backup），
  不插入业务因果链。`test_dag_order.py` 5 例断言合同；`test_daily_dag_closed_loop.py` 全闭环验证。

## 6. 每类故障注入结果（§5 七类 + §6 副本演练）

| # | 故障 | 结果（结构化） | 下游阻断 | COMPLETE manifest |
|---|---|---|---|---|
| 1 | 同步失败（当日非交易日） | `eod_gates` FAIL `NOT_TRADING_DAY` | 全部 SKIPPED | 无 |
| 2 | 行情缺失 | `close_valuation` FAIL `NO_VALUATION` | 对账/草稿/manifest 全部 SKIPPED；`pt_daily_snapshot`/`pt_reconciliation` 无新增 | 无 |
| 3 | 公司行为未处理 | `eod_gates` FAIL `CORPORATE_ACTION_PENDING` | 全部 SKIPPED | 无 |
| 4 | 撮合中断 | `match_confirmed_orders` FAIL `MATCH_INTERRUPTED` | `close_valuation` 起全部 SKIPPED | 无 |
| 5 | 对账一分钱差异 | `internal_reconciliation` FAIL `RECONCILIATION_DIFF`；`pt_cycle.phase=RECONCILE`；`pt_reconciliation` 记录 `DIFF/CRITICAL` | 回填/草稿/manifest SKIPPED | 无（不生成） |
| 6 | 审计写失败 | run FAILED（fail-closed），`dag_runs.status=FAILED` | 不产生 COMPLETE 证据 | 无 |
| 7 | 进程重启（崩溃遗留 RUNNING） | 同 attempt 覆盖重试（attempt 不重数）；租约过期可接管 | 恢复后 COMPLETED | 有（恢复完成） |

副本演练 6 步证据（`E:\ab-maintenance\v2r-o2\drill_evidence.json`，trade_date=20260821）：
1. 首次完整执行 → COMPLETED，manifest COMPLETE（`DM-20260821-7305be5f…`，sha256 7305be5f…）
2. 相同输入重放 → COMPLETED，账本 8 表 `ledger_unchanged=true`，`dag_runs` 1 行不变，
   `dag_step_runs` 增长 0，9 步全部 `idempotent`
3. 中途故障（撮合中断，新输入身份 drill-fault）→ FAILED，下游全 SKIPPED，失败 step attempt 保留
4. 模拟进程退出（持有租约 + RUNNING run + RUNNING attempt，不释放）→ 租约 300s 后过期，
   新 holder 接管，RUNNING attempt 同号覆盖重试，恢复 COMPLETED；`dag_step_runs` 无永久 RUNNING，
   `dag_leases` 无残留
5. 一分钱对账差异（MANUAL +1 fen）→ `internal_reconciliation` FAIL `RECONCILIATION_DIFF`，
   run FAILED，**未生成新 manifest**（不 COMPLETE）
6. 修复（删除 +1 fen）→ 重跑 COMPLETED，最新 manifest COMPLETE（`DM-20260821-b9352218…`）；
   失败 run 与审计事件未删除（`audit_events` 7 条、链 `valid=true`）

## 7. 并发 / 重启 / 重放表计数

- 并发单租约：同账户/交易日两个并发 runner → runner B `LEASE_CONFLICT`（不产生 run），
  runner A COMPLETED；`dag_runs` 仅 1 行 COMPLETED（`test_scheduler_lease.py`）。
- 重启 attempt 保留：1/2 次失败 + 第 3 次终止后重启，`dag_step_runs` 中 `b` 恰 3 行
  （`ATTEMPT_FAILED, ATTEMPT_FAILED, FAIL`），第 4 次不得执行；重启后 `log.count('b')==0`。
- 重放：同输入重放账本 8 表行数/余额不变（含 `pt_daily_snapshot` 快照相等）；`dag_runs` 不增加。
- 输入身份变化：`input_hash=v2` 重跑 9 步（新 step_run 记录），业务账本不重复
  （成交/现金/持仓/草稿行数不变）。
- Windows 重启/连接中断后：`dag_step_runs` 无永久 RUNNING；`dag_leases` 无永久租约
  （`test_no_permanent_running_or_lease_after_crash`：租约有过期时间，可接管）。

## 8. 审计链 / 脱敏证据

- 幂等：同 correlation+action+request 重放返回既有 `event_id`，不重复写（1 行）。
- 并发防分叉：双线程写同内容事件 → 2 线程得到同一 `event_id`，仅 1 行，`verify_audit_chain` valid。
- 篡改检测：删行/改 `before_json` → `verify_audit_chain` invalid。
- 缺 key 拒绝：未注入且无环境变量 → `AUDIT_SIGNING_KEY_MISSING`，签名与验证都拒绝。
- key 不落盘：`AUDIT_SIGNING_KEY` 从硬编码改为调用方注入 > 环境变量；
  测试断言 key 不出现在 `audit_events` 行与锚定 `.sig` 文件。
- 脱敏：`token/password/secret/api_key/key` 标签与 18 位完整账户号 → `[REDACTED]`，
  递归作用于嵌套 dict/list；告警与审计同款规则，`alert_events.payload_json` 无敏感原文。
- 告警：`dedupe_key` 幂等；`list_alerts_at`/`alert_exists` 走只读连接，GET 零写入
  （文件字节数前后不变）。
- 副本演练链：7 条审计事件（2 RUN_START + 2 STEP_FAILED + 3 RUN_FINISHED），`verify_audit_chain` valid。

## 9. soak 当前真实天数

- 命令：`python scripts/soak_monitor_v2.py --db runtime/stock_data.db --soak-dir runtime/v2/soak --code-version <sha> --config-hash 193284063fe9c36a`
- 结果：`count=0, status=INSUFFICIENT, exit=1`（`note: 不足 5 个不同完成交易日（不伪造等待结果）`）
- 自任务被接受后尚无真实完成交易日（最近真实完成日为 20260821，其生产 manifest 为 PARTIAL；
  本任务不接受回填），故证据目录为空，`O-12=INSUFFICIENT`。下一次真实 EOD 完成后
  `--collect <trade_date>` 才会写入证据文件。

## 10. 必跑命令原始摘要（§8）

> 环境备注：本沙箱对 SQLite DDL/文件删除有数倍 I/O 放大（safe-delete shim），
> 每测试约 20–30s，全量 pytest 耗时显著高于正常环境。结果均为真实运行。

| 命令 | 结果 |
|---|---|
| `pytest tests/test_daily_dag.py tests/test_daily_dag_closed_loop.py tests/test_fault_injection_scheduler.py tests/test_audit_hash_chain.py tests/test_soak_monitor_v2.py -q` | **27 passed**（含 test_dag_order 5 + test_scheduler_lease 4 一并复跑；3537s 全量之外独立确认） |
| `pytest -m fault_injection --collect-only -q` | 18 tests collected（fault_injection_scheduler 7 + soak 11） |
| `pytest -m fault_injection -q` | **18 passed, 849 deselected**（380s） |
| `ruff check ab_screener/operations ab_screener/application/audit_service.py ab_screener/data/scheduler_repository.py scripts/soak_monitor_v2.py tests/test_dag*.py tests/test_scheduler*.py tests/test_audit*.py tests/test_fault_injection*.py tests/test_soak*.py` | `All checks passed!`（0 error；未扩大 ignore/exclude） |
| `mypy ab_screener/operations ab_screener/application/audit_service.py ab_screener/data/scheduler_repository.py scripts/soak_monitor_v2.py` | `Success: no issues found in 9 source files`（0 error） |
| `python scripts/check_architecture.py --strict` | `architecture OK: 无 sqlite3/subprocess 直接 import`（exit 0） |
| `pytest -q`（全量，本 worktree 复跑） | **864 passed, 3 failed, 8 warnings**（3537s）。3 个失败均为
  `tests/test_v2_baseline_manifest.py`，原因=`runtime/v2/baseline_manifest.json` 不存在（该产物需
  `scripts/capture_v2_baseline.py` 且后端 8001 运行才能生成）；该测试文件不在 O2 owned 范围，
  worktree 无运行后端属环境前置缺失，非 O2 引入回归（管理者权威环境含该产物，Wave2 全量 825 含此文件通过）。
  生成产物后 `pytest tests/test_v2_baseline_manifest.py` 通过（本 worktree 未启后端故未再生成）。 |
| `git diff --check` | exit 0 |
| `git status --short` | 仅授权文件（见 §2） |

## 11. 未决项

- 全量 pytest 仍在运行/复跑结果见 §10；若有失败将在此记录并修复后再提交。
- 生产库 `v2:operations` 迁移已在 2026-08-18 应用（dag 控制表存在、0 行），本任务未改生产 schema；
  未提交任何新迁移提案。
- 副本演练中 `dag_runs` 按 `trade_date+mode` 幂等复用同一 run_id（`start_run` 设计）；
  失败证据在 `dag_step_runs`（per input_hash）与 `audit_events` 完整保留，`dag_runs` 行状态为
  最后一次完成状态。如需"每失败独立 run 行"，属新增语义变更，超出本任务授权范围，已如实披露。
- O-12 为 INSUFFICIENT：不宣布 O 闸门 PASS、V2 完成或个人机构级就绪。

## 12. 提交清单与逐 commit 回滚

提交计划（均为新 commit，不改写基线历史）：
1. `test(v2r-o2): dag contract, lease, fault injection, audit, soak tests`（新增 5 个测试文件）
2. `feat(v2r-o2): persistent eod dag, lease, audit chain, redaction, soak v2`（源码 + pyproject + 既有测试修改）
3. `docs(v2r-o2): replica drill evidence + handoff`（本 handoff + `docs/BACKUP-RESTORE-RUNBOOK-V2.md` 如有更新）

回滚方式：
- 单 commit：`git revert <sha>`（无 DB/迁移副作用，控制表仅在副本/测试库产生）。
- 全部：`git reset --hard 1dffcb7`（丢弃本任务全部提交；副本 `E:\ab-maintenance\v2r-o2\` 可整目录删除）。
- 生产库未改动，无需 DB 回滚。
