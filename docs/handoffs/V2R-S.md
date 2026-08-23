# V2R-S Agent 交付

> 本文件由实现 Agent 填写。自报完成不等于验收通过；管理者复验后才会更新任务板。

## 1. 身份

- 任务 ID：V2R-S（六形态扫描、不可变信号、fill/outcome 生产接线）
- Agent 角色：signal-lifecycle-agent
- 基线 commit：`7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe`（任务板/启动包指定 base）
- 分支：`v2r-s`
- worktree 绝对路径：`E:\CODEX\Stock_selection\worktrees\v2r-s`
- 交付 commit：`681ef56`（feat 代码/测试；其上有 docs 补记 commit）
- 开始/完成时间（Asia/Shanghai）：2026-08-23（同日完成）

## 2. 范围核对

- 实际修改文件（`git diff --name-only`，base..HEAD）：

```
ab_screener/api/routers/legacy_scan.py
ab_screener/application/signal_outcomes.py
ab_screener/application/signal_pipeline.py
ab_screener/data/signal_repository.py
ab_screener/domain/signal_lifecycle.py
tests/test_signal_fill_lifecycle_integration.py（新建）
tests/test_signal_pipeline_production_wiring.py（新建）
docs/handoffs/V2R-S.md（本文件）
```

- 是否全部位于 owned_paths：是（`ab_screener/strategies/**` 未改动、`ab_screener/application/signal_pipeline.py`、`signal_outcomes.py`、`ab_screener/domain/signal*.py`、`ab_screener/data/signal*.py`、`ab_screener/api/routers/legacy_scan.py`、`tests/test_signal*.py`、`tests/test_strategy*.py`、`docs/handoffs/V2R-S.md`）
- 是否触碰 protected/shared paths：否（未改 `web/backend_app.py`、`app_factory.py`、`configs/platform_v2.yaml`、前端 dist、`docs/STATUS.md` 等）
- 未解决的工作区变更：无

原始输出：

~~~powershell
git status --short
 M ab_screener/api/routers/legacy_scan.py
 M ab_screener/application/signal_outcomes.py
 M ab_screener/application/signal_pipeline.py
 M ab_screener/data/signal_repository.py
 M ab_screener/domain/signal_lifecycle.py
?? tests/test_signal_fill_lifecycle_integration.py
?? tests/test_signal_pipeline_production_wiring.py
~~~

~~~powershell
git diff --stat <base>..<681ef56>
 ab_screener/api/routers/legacy_scan.py     | 166 ++++++++++++++++++++++++++++-
 ab_screener/application/signal_outcomes.py | 111 +++++++++++++++++++
 ab_screener/application/signal_pipeline.py | 107 ++++++++++++++++++-
 ab_screener/data/signal_repository.py      |  12 ++-
 ab_screener/domain/signal_lifecycle.py     |  10 ++
 tests/test_signal_fill_lifecycle_integration.py      | 206 ++++++++++++++++++++++++
 tests/test_signal_pipeline_production_wiring.py     | 352 ++++++++++++++++++++++++++++++++++++
 5 files changed, 396 insertions(+), 10 deletions(-)（另 2 个新测试文件共 558 行）
~~~

## 3. 根因与设计

- 原始失败或缺口：
  1. 六插件已注册并能产生 `SignalObservation`，但没有“扫描完成后按 scan_run 落库不可变观察”的生产路径（`legacy_scan` 完成后不写 signal_observations）。
  2. `run_signal_pipeline` 的“幂等”只体现在行数不变，`saved_count` 把重放跳过的 id 也计入，无法区分“新插入”与“重放跳过”。
  3. 没有任何 A 池资格闸门：EXPERIMENTAL 命中后是否进入 A 池/买入草稿/目标仓位没有显式硬隔离。
  4. 没有任何 fill→ENTERED 的生产接线：`append_event` 有状态机护栏，但没有“订单确认/零成交/拒绝/过期不得伪装成成交”的明确入口。
  5. outcome 没有时点门：ret_5/10/20 何时可回填（交易日完成 + 行情 `available_at <= calculation_at`）没有显式实现。
- 根因：信号产生/落库（P4.1/P4.2）与生命周期（P4.3）各自存在，但缺少“扫描→观察→A 池闸门→fill→outcome 时点”的显式生产接线函数与测试。
- 采用方案（最小接线，全部在 owned_paths）：
  - `domain/signal_lifecycle.py`：新增领域规则 `fill_qualifies_for_entered(filled, qty)` —— 只有 `filled=True 且 qty>0` 才算成交。
  - `data/signal_repository.py`：把 `save_observation` 拆出 `insert_observation(conn, obs) -> bool`（返回是否真正插入），`save_observation` 保持原返回契约不变。
  - `application/signal_pipeline.py`：
    - `A_POOL_REQUIRED_STATUS="ACTIVE_FOR_A_POOL"`、`is_a_pool_eligible(spec)`、`a_pool_candidates(observations)`（未知策略 id fail-closed）。
    - `apply_fill_to_signal(conn, observation_id=, fill=, order_state=)` —— CONFIRMED/QUEUED、无 fill、零成交、拒绝、过期都不进入；正数量 fill 追加 ENTERED 事件（actor=fill）。
    - `run_signal_pipeline` 返回新增 `saved_observations`、`a_pool_eligible_ids/count`，且只统计新插入（重放跳过不计入）。
  - `application/signal_outcomes.py`：
    - `compute_horizon_result(...)` —— 交易日完成 + `available_at <= calculation_at` 双门；UNFILLABLE/PENDING 一律 NULL（不填 0）。
    - `backfill_horizon_outcome(...)` —— 生产接线，写 `signal_outcomes`（修订追加、重放幂等不覆盖历史行）。
  - `api/routers/legacy_scan.py`：
    - `persist_scan_signals(...)` —— 受 `V2_STRATEGY_REGISTRY_ENABLED` 门控（默认 false → no-op），scan_run_id 作 snapshot_id，重放幂等。
    - `_candidate_codes_from_result`（扫描结果→候选代码）、`_read_daily_bars(db_path, code, as_of=...)`（按 as_of 截断，防未来函数）。
    - worker 在 `complete_scan_run` 成功后调用 hook（门控默认关闭，生产行为不变）。
- 未采用方案及原因：
  - 把信号管线直接塞进子进程扫描（orchestrator/scan_job_runner）：超出 owned_paths（属 V2R-A），且重放/审计路径复杂。
  - 改 `save_observation` 返回类型（如 tuple）：破坏既有测试契约（`test_signal_observations.py` 断言返回值等于 observation_id）。
  - 在 outcome 中引入交易日历依赖：`compute_horizon_result` 保持纯函数，交易日完成性由调用方显式传入，避免本任务引入日历服务。
- 是否改变 API、表结构、配置、策略语义、成交语义或风险语义：
  - **表结构：否**（未新增/修改任何表，复用 `v2:signals` 迁移的 signal_observations/events/projection/outcomes）。
  - **API 路由：否**（`legacy_scan.py` 只新增模块级函数 + worker 内门控 hook；不新增/修改 HTTP 端点，默认 flag=false 时 hook 为 no-op）。
  - **配置：否**（未改 `configs/platform_v2.yaml`；复用既有 `V2_STRATEGY_REGISTRY_ENABLED` flag，默认 false）。
  - **策略语义：否**（六插件未改动）。
  - **成交语义：否**（`apply_fill_to_signal` 是新增入口；`FillV2`/`compute_fill` 语义未改）。
  - **风险语义：否**。

涉及交易/研究时必须回答：

- decision_at 与 available_at 如何保证：
  - outcome 时点门：`compute_horizon_result` 要求 `available_at <= calculation_at` 且 `maturity_trade_date <= last_completed_trade_date`，否则返回 NULL。
  - 生产 bars 读取：`_read_daily_bars(db_path, code, as_of=...)` 按 `trade_date <= as_of` 截断（worker 总是传 scan 的 latest_date），有专项测试 `test_read_daily_bars_bounded_by_as_of`。
- 是否存在同收盘信号同收盘成交路径：否（fill 接线只消费 v2 `FillV2`；`order_semantics.assert_no_same_close_fill` 语义未触碰；本任务不新增成交路径）。
- 金额是否保持整数分/定点价格：是（`compute_horizon_result`/`compute_outcome` 使用整数 micro；`record_outcome` 存储 micro；未引入浮点账务输入）。
- 是否改变 A/B 池资格或订单生成：否（A 池闸门是“只读过滤”，六插件全部 EXPERIMENTAL → `a_pool_candidates` 恒为空；不生成买单、不改变目标仓位；有专项测试断言 signal_events/signal_outcomes 零写入）。
- LIVE_TRADING_ENABLED 是否仍为 false：是（未触碰任何 flags；`persist_scan_signals` 受 `V2_STRATEGY_REGISTRY_ENABLED` 门控，默认 false，fail-closed）。

## 4. TDD 证据

### 失败测试

- 测试名称：`tests/test_signal_pipeline_production_wiring.py`（11 用例）、`tests/test_signal_fill_lifecycle_integration.py`（9 用例，含 1 个收集期错误）
- 修改前命令：`python -m pytest tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py -q`
- 修改前预期与实际（base 快照，仅新增两个测试文件）：

```
tests\test_signal_pipeline_production_wiring.py::test_replay_same_scan_run_produces_single_observation FAILED (ImportError: cannot import name 'persist_scan_signals')
... 共 11 failed（全部 ImportError：persist_scan_signals / a_pool_candidates / is_a_pool_eligible /
    A_POOL_REQUIRED_STATUS / compute_horizon_result / backfill_horizon_outcome 不存在）
11 failed in 35.96s

tests\test_signal_fill_lifecycle_integration.py: ERROR collecting ...
E   ImportError: cannot import name 'fill_qualifies_for_entered' from 'ab_screener.domain.signal_lifecycle'
1 error in 1.00s
```

（完整 RED 输出见 `/tmp/v2rs_red_evidence.txt`，本机临时文件，未提交。）

### 最小实现

- 关键实现文件和入口：
  - `ab_screener/domain/signal_lifecycle.py`：`fill_qualifies_for_entered`
  - `ab_screener/data/signal_repository.py`：`insert_observation`
  - `ab_screener/application/signal_pipeline.py`：`is_a_pool_eligible` / `a_pool_candidates` / `apply_fill_to_signal`；`run_signal_pipeline` 只统计新插入并返回 A 池资格
  - `ab_screener/application/signal_outcomes.py`：`compute_horizon_result` / `backfill_horizon_outcome`
  - `ab_screener/api/routers/legacy_scan.py`：`persist_scan_signals`（门控） + worker hook
- 幂等策略：
  - observation：同 `scan_run_id(snapshot_id) + strategy_version(input_hash) + ts_code + signal_date` → 同 observation_id → 重放跳过（`insert_observation` 返回 False）。
  - outcome：同 (observation, horizon) 最新行状态与数值一致时跳过写入（不覆盖历史行）；修订追加路径保留（修正数据产生新 revision）。
- 失败模式：未知策略 id 过 A 池闸门 → fail-closed 抛错；非 ORDER_CREATED 收到 fill → `SignalLifecycleError`；outcome 时点非法 → NULL 不填 0。
- 日志/审计：未新增独立审计；signal_events 记录 ENTERED（actor=fill，payload 含 FillV2.to_dict()）。

### 通过测试

逐条粘贴命令与摘要：

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_signal_observations.py tests/test_signal_lifecycle_v2.py tests/test_signal_outcomes.py tests/test_signals_v2.py tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py -q
# 42 passed in 354.53s
~~~

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py -q
# 21 passed in 181.63s（12 + 9 新用例）
~~~

说明：每个测试夹具新建 tmp_path SQLite 并跑 `apply_pending`（全量 v2 迁移），单文件约 12–32s，属环境固有开销（与 base 基线一致）。

## 5. 质量证据

- 定向 Pytest：上面 42 passed。
- Ruff：`ruff check ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py ab_screener/api/routers/legacy_scan.py` → **All checks passed!**（顺带修复了 legacy_scan.py 既有的 2 个 ruff 错误：I001 import 排序、F401 `pydantic.Field` 未使用）
- Mypy：`mypy ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py` → **Success: no issues found in 13 source files**
- OpenAPI/契约测试：未运行（本任务无 HTTP 端点/OpenAPI 改动；legacy_scan 仅新增模块级函数与门控 hook）
- 前端 build/test/E2E：未运行（本任务无前端改动）
- 性能数字：42 用例含迁移开销 ~5m54s；纯断言无新增重型计算。
- 数据库副本/fixture：全部 `tmp_path` 临时库（见第 6 节）；未触碰生产库。

补充验收命令（启动包必跑）：

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_order_risk_integration.py tests/test_execution_dual_run_integration.py -q
# 12 passed in 31.66s
~~~

~~~powershell
git diff --check   # OK（无空白错误）
git status --short # 仅 owned_paths 变更
~~~

交叉回归（改动消费方）：

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_scan_funnel.py -q            # 4 passed（run_signal_pipeline 消费方）
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_closers_e2_split_regressions.py -q  # 3 passed（legacy_scan 既有 import 回归）
~~~

## 6. 数据与运行证据

- 数据库路径：**非生产库副本**。本任务所有测试使用 pytest `tmp_path` 临时 SQLite（模式：`C:\Users\13818\AppData\Local\Temp\pytest-of-bobo\pytest-*\*.db`，如 `prod_wiring.db`、`fill.db`、`risk.db`、`sig.db` 等）。未读写生产账本 `runtime/stock_data.db` 或任何生产 DB；worktree `runtime/stock_data.db` 为工作区初始化空 schema，未被本任务修改。
- 数据日期：合成 K 线（`test_signals.make_synthetic`，signal_date 落在 2026-04/05）；outcome 时点测试使用显式 `maturity_trade_date`/`last_completed_trade_date`/`available_at`/`calculation_at`（如 20260810–20260818），不依赖真实行情。
- 数据库 fingerprint：临时库随 pytest 清理，无保留副本；`V2_STRATEGY_REGISTRY_ENABLED=false` no-op 测试断言 0 写入（观察/事件/outcome 全 0）。
- 代码 SHA：base `7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe`；delivery `681ef56`（含全部代码/测试）。
- config hash：未改动（未触碰 config 文件；复用 `V2_STRATEGY_REGISTRY_ENABLED`）。
- 产物路径：无提交产物（golden 走 tmp_path）。
- 产物 SHA-256：不适用（无产物文件提交）。
- 是否访问外部数据源：否（全部离线合成 fixture；插件 legacy 引擎在本地运行）。
- 是否包含 Token/账户号：必须为否 —— 无任何 token/账户写入文件。

### PIT 证明

1. `test_read_daily_bars_bounded_by_as_of`：生产 bars reader 在 `as_of="20260810"` 时只返回 ≤20260810 的 K 线，20260811 被截断。
2. `test_ret_null_when_market_data_available_at_out_of_bounds`：`available_at=2026-08-18T16:00` > `calculation_at=2026-08-17T16:00` → ret NULL（不回填）。
3. `test_ret_backfilled_when_trading_day_complete_and_pit_ok`：`available_at <= calculation_at` 且交易日完成 → MATURED 回填。

### 重放幂等证明

1. `test_replay_same_scan_run_produces_single_observation`：同 scan_run 重放第二次 `persisted == 0`，观察/事件/outcome 行数不变。
2. `test_new_scan_run_revision_appends_and_old_rows_unchanged`：新 scan_run 追加新 observation，旧行 `observed_at`/`payload` 逐字段不变。
3. `test_backfill_horizon_outcome_wiring_idempotent`：outcome 重放幂等（`idempotent=True`），PENDING(rev1)+MATURED(rev2) 历史行不被覆盖。

## 7. 回滚

- 回滚 commit：`git revert 681ef56`（本分支独立，不合并主分支；管理者可整体丢弃 `v2r-s` 分支）。
- 配置回滚：无配置改动。
- 数据回滚或冲正：无（未写生产库；临时库随 pytest 清理，可整体删除）。
- 是否需要停止服务：否（默认 flag=false，扫描 worker 行为与 base 一致）。
- 是否存在不可逆操作：否。`git reset --hard 7bbca60` 可完整回到基线（worktree `runtime/` 为 gitignore，不影响）。

## 8. Agent 自评

- 建议管理者判定：待验收（READY_FOR_REVIEW）
- 已知缺陷：
  1. `persist_scan_signals` 门控关闭时是 no-op，真实生产接线需要管理者在 O2 释放后翻转 `V2_STRATEGY_REGISTRY_ENABLED`；届时建议先做一次真实扫描 + 落库演练。
  2. `_candidate_codes_from_result` 对 `pool_report` 结构做了防御性猜测（`hits` 字段优先；`pool_a/pool_b/a_codes/b_codes` 兜底）。扫描子进程结果字段以 `scan_job_runner` 实际输出为准，门控开启前建议核对一次。
  3. `compute_horizon_result` 的交易日完成性由调用方显式传入（`last_completed_trade_date`），本任务未引入交易日历服务；O2 的 EOD DAG 需提供该输入。
- 后续依赖：V2R-O2 释放条件所需的五个行为（信号幂等、fill 驱动 ENTERED、outcome 时点合法、EXPERIMENTAL 硬隔离、定向测试/Ruff/Mypy）均已满足；EOD DAG 的 signal outcome 步骤可直接调用 `backfill_horizon_outcome`。
- 明确声明：本 Agent 未宣布 PERSONAL_INSTITUTIONAL_READY；未启动 V2R-O2；未修改任务板。

## 9. 管理者区（实现 Agent 不填）

- 范围审查：
- 代码审查：
- 定向复验：
- 交叉域复验：
- 运行态复验：
- 判定：ACCEPTED / REWORK_REQUIRED / REJECTED
- 缺陷编号：
- 允许进入的下一任务：
