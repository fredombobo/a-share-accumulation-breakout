# V2R-A Agent 交付

> 本文件由实现 Agent 填写。自报完成不等于验收通过；管理者复验后才会更新任务板。

## 1. 身份

- 任务 ID：V2R-A（扫描内核拆分与确定性回归）
- Agent 角色：scanner-architecture-agent
- 基线 commit：`b6772c3001e1fa37447fca813b7ad3512b54eb49`（任务板指定 base）
- 分支：`agent/v2r-a`
- worktree 绝对路径：`E:\CODEX\Stock_selection\worktrees\v2r-a`
- 交付 commit：`87a678be861dd891d575fddcf894440e1bf7846e`（refactor，含全部代码/测试；其上有 docs 补记 commit）
- 开始/完成时间（Asia/Shanghai）：2026-08-23（同日完成）

## 2. 范围核对

- 实际修改文件（`git diff --name-only`，base..HEAD）：

```
ab_screener/run_screener.py
ab_screener/screener/__init__.py
ab_screener/screener/data_loader.py
ab_screener/screener/prefilter.py
ab_screener/screener/evaluator.py
ab_screener/screener/orchestrator.py
run_screener.py
tests/test_screener_architecture.py
tests/test_screener_golden_result.py
docs/handoffs/V2R-A.md
```

- 是否全部位于 owned_paths：是（`ab_screener/run_screener.py`、`ab_screener/screener/**`、`run_screener.py`、`tests/test_*screener*.py`、`docs/handoffs/V2R-A.md`）
- 是否触碰 protected/shared paths：否
- 未解决的工作区变更：无（`runtime/` 下 scratch 脚本仅本地调试用，已 gitignore，不提交）

原始输出：

~~~powershell
git status --short
 M ab_screener/run_screener.py
 M run_screener.py
?? ab_screener/screener/
?? tests/test_screener_architecture.py
?? tests/test_screener_golden_result.py
~~~

~~~powershell
git diff --stat <base>..<HEAD>
 ab_screener/run_screener.py | 1077 ++------------------------------------------
 run_screener.py             |    3 +-
 ab_screener/screener/__init__.py    | 45 +++++++++++++++++++++++++++
 ab_screener/screener/data_loader.py| 87 +++++++++++++++++++++++++++++
 ab_screener/screener/prefilter.py  | 46 +++++++++++++++++++++
 ab_screener/screener/evaluator.py  | 520 ++++++++++++++++++++++++++++++++++++++
 ab_screener/screener/orchestrator.py| 516 ++++++++++++++++++++++++++++++++++++++
 tests/test_screener_architecture.py| 105 +++++++++++++++++++++++
 tests/test_screener_golden_result.py| 289 ++++++++++++++++++++++++++++++
 docs/handoffs/V2R-A.md              |  1 +（本文件）
~~~

## 3. 根因与设计

- 原始失败或缺口：`ab_screener/run_screener.py` 1100 行单块实现，数据加载/候选/单标的结果/编排四类职责混在同一个模块；扫描结果缺乏可复验的确定性回归（golden），后续任何重构都缺少“结果逐字段不变”的护栏。
- 根因：模块职责未分层；结果确定性没有被测试冻结。
- 采用方案：按主计划 Task 1A 拆为 `ab_screener/screener/` 四个职责单一模块 + 薄 facade：
  - `data_loader.py`：只读/标准化输入（`load_market_data`，新增可选 `db_path` 注入）。
  - `prefilter.py`：候选集合 + 理由（`prefilter`）。
  - `evaluator.py`：单标的结果（`apply_box_ladder` / `_score_codes` / `_soft_setup_row` / `_theme_soft_fill` / `observed_signal` / `_detect_on_codes`）。
  - `orchestrator.py`：进程/取消/进度/排序/聚合（`run_scan` 主体，新增可选 `store` / `as_of` 确定性后门）。
  - `ab_screener/run_screener.py`：公共 facade（121 行 < 350），`main` + 转发。
  - 根 `run_screener.py`：保持薄 re-export（仅修正 ruff noqa）。
- 未采用方案及原因：
  - 一次性把 store 注入做进 `data_fetch` 模块级：会改动共享数据层，超出 owned_paths。
  - 不拆 facade 直接重写 import：会破坏 `scan_job_runner` 子进程 spawn 与 `audit_funnel` 等旧 import。
- 是否改变 API、表结构、配置、策略语义、成交语义或风险语义：否。
  - `run_scan` 新增 `* , store=None, as_of=""` 关键字参数（默认值 None/空串），不传时行为与旧版逐分支一致（AST 对比 + 同 fixture 全字段一致验证）。
  - `load_market_data` 新增可选 `db_path=None`，默认仍走生产库。
  - ENTRY、评分公式、阈值、默认参数、结果格式、A/B 池拆分完全不变。

涉及交易/研究时必须回答：

- decision_at 与 available_at 如何保证：本任务不涉及交易写入；扫描只读行情 + 写 scan_result，未改变任何 PIT 语义。
- 是否存在同收盘信号同收盘成交路径：不涉及。
- 金额是否保持整数分/定点价格：不涉及。
- 是否改变 A/B 池资格或订单生成：否（golden 逐字段断言覆盖 A/B 池）。
- LIVE_TRADING_ENABLED 是否仍为 false：是（未触碰任何 flags）。

## 4. TDD 证据

### 失败测试

- 测试名称：`tests/test_screener_architecture.py`、`tests/test_screener_golden_result.py`（新写，先失败后通过）
- 修改前命令：`python -m pytest tests/test_screener_architecture.py tests/test_screener_golden_result.py -q`（在 base b6772c3 快照上执行）
- 修改前预期与实际：

```
ImportError while importing test module '...\tests\test_screener_golden_result.py'.
E   ModuleNotFoundError: No module named 'ab_screener.screener'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 1.29s
```

即：旧代码不存在 `ab_screener/screener`，且 `run_scan` 不接受 `store`/`as_of`。

### 最小实现

- 关键实现文件和入口：
  - `ab_screener/screener/{__init__,data_loader,prefilter,evaluator,orchestrator}.py`（纯职责拆分）
  - `ab_screener/run_screener.py` facade；根 `run_screener.py` shim
- 幂等策略：`run_scan` 两次对同一冻结市场结果逐字段一致（golden 测试）；scan_result 写入沿用“先清当日再写”。
- 失败模式：业务逻辑迁移后若结果漂移，golden 测试逐字段断言立即失败。
- 日志/审计：未新增（沿用原打印）。

### 通过测试

逐条粘贴命令与摘要（不写“测试已通过”这种无证据描述）：

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_screener_architecture.py tests/test_screener_golden_result.py tests/test_scan_spawn.py tests/test_scan_runtime.py tests/test_scan_progress_io.py tests/test_scan_guard.py -q
# 32 passed, 6 warnings in 26.85s
~~~

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests/test_upgrade_system.py tests/test_architecture_boundaries.py -q
# 44 passed in 6.68s（旧 import / run_screener 消费方回归）
~~~

## 5. 质量证据

- 定向 Pytest：`tests/test_screener_architecture.py tests/test_screener_golden_result.py tests/test_scan_spawn.py tests/test_scan_runtime.py tests/test_scan_progress_io.py tests/test_scan_guard.py` → **32 passed**。
- Ruff：`ruff check ab_screener/run_screener.py ab_screener/screener run_screener.py` → **All checks passed!**（0 errors）
- Mypy：`mypy ab_screener/run_screener.py ab_screener/screener` → **Success: no issues found in 6 source files**
- OpenAPI/契约测试：未运行（本任务无 API 改动）
- 前端 build/test/E2E：未运行（本任务无前端改动）
- 性能数字：golden 全套 4 个用例 3.13s（含两次完整 run_scan + 单/多 worker 对比）
- 数据库副本/fixture：golden 使用临时 SQLite 冻结市场（见第 6 节）

## 6. 数据与运行证据

- 数据库路径：**非生产库副本**。本任务未触碰生产库（`E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db`，约 16.3 GB）。worktree `runtime/stock_data.db` 为工作区建立时由迁移初始化（仅 schema，无行情），未被我修改。
- 数据日期：冻结市场 as_of=20260807（合成数据）；不依赖真实行情。
- 数据库 fingerprint（worktree 空 schema 库）：`sha256 561b5c2861c9cac3bea03872a87543818164b52db814c9f284ca58d652af2ec1`
- 代码 SHA：base `b6772c3…`；head `87a678be861dd891d575fddcf894440e1bf7846e`（含全部代码/测试）。
- config hash（config.py）：`af5559cdc41919bb`（未改动 config）
- 产物路径：golden fixture 走 pytest tmp_path；无提交产物。
- 产物 SHA-256：不适用（无产物文件提交）。
- 是否访问外部数据源：否（全部离线合成 fixture；子进程 spawn 探测仅验证 import 链，未访问外部）。
- 是否包含 Token/账户号：必须为否 —— 无任何 token/账户写入文件。

## 7. 回滚

- 回滚 commit：`git revert <HEAD>`（本分支独立，不合并主分支；管理者可整体丢弃该分支）。
- 配置回滚：无配置改动。
- 数据回滚或冲正：无（未写生产库；scan_result 写入了临时冻结库，可整体删除）。
- 是否需要停止服务：否（仅代码拆分，无运行态变更）。
- 是否存在不可逆操作：否。`git reset --hard b6772c3` 可完整回到基线（runtime/ 为 gitignore，不影响）。

## 8. Agent 自评

- 建议管理者判定：待验收
- 已知缺陷：
  1. 根 `run_screener.py` 被直接 `python run_screener.py` 执行时不做任何事（base 即如此，shim 仅用于 import 兼容）；CLI 入口为 `python -m ab_screener.run_screener`。
  2. `test_screener_golden_result.py` 触发一条 pandas FutureWarning（`pd.concat` 空/全 NA 列），为 base 已有代码行为，未改。
  3. WorkBuddy sandbox 的 safe-delete 在 pytest 临时目录清理时可能 fail-closed 报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（环境问题，测试本体通过；本任务未新增临时文件清理逻辑）。
- 后续依赖：V2R-Q2 集成质量债务（如需）可复用本拆分；scan_result 持久化仍建议由后续任务接入应用层。
- 明确声明：本 Agent 未宣布 PERSONAL_INSTITUTIONAL_READY。

## 9. 管理者区（实现 Agent 不填）

- 范围审查：PASS；base/head 和 owned paths 符合，未触碰共享入口、配置、数据 adapter 或 dist。
- 代码审查：PASS；facade 121 行、根 shim 保留，ENTRY/阈值/A-B 池语义未发现改动。
- 定向复验：scanner golden、spawn、取消、进度、单/多 worker 等 59 passed；Ruff 0；Mypy 0。
- 交叉域复验：strict architecture、upgrade system、architecture boundaries 通过。
- 运行态复验：全部使用 fixture/临时库，无生产写入。
- 判定：ACCEPTED
- 缺陷编号：无阻断；pandas concat FutureWarning 交 Q2。
- 允许进入的下一任务：V2R-A 依赖视为完成；Wave 2 仍受 D/X 等返工阻断。
- 完整记录：`docs/ACCEPTANCE-V2-REMEDIATION-WAVE1-2026-08-23.md`。
