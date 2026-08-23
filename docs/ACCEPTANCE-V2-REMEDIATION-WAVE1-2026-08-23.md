# v2.0 纠错工程 Wave 1 独立验收

- 验收时间：2026-08-23 14:52 Asia/Shanghai
- 固定基线：`b6772c3001e1fa37447fca813b7ad3512b54eb49`
- 管理结论：`REWORK_REQUIRED`
- 已接受：`V2R-Q1`、`V2R-A`
- 需返工：`V2R-D`、`V2R-X`、`V2R-F`、`V2R-O1`
- Wave 2：不释放；第一波六项未全部接受
- 安全结论：未发现开启真实交易、PIT 正式读、v2 执行写、风险 enforce 或日调度的提交；未发现凭据进入提交；未合并任何 Wave 1 分支；未写生产数据库或删除备份。

## 1. 身份与范围

六个实现均从固定基线建立独立 worktree，交付分支工作树在验收开始前干净；提交改动均位于任务所有权范围及对应 handoff。`web/frontend/dist` 未进入 V2R-F 提交。管理者运行前端 build 后在该 worktree 产生的本地 dist/test-results 不是交付提交，返工时不得纳入提交。

| 任务 | 复验 head | 结论 | 下一步 |
|---|---|---|---|
| V2R-Q1 | `8310b08aeb67` | ACCEPTED | 依赖项视为完成 |
| V2R-A | `26f77ebf5c7b` | ACCEPTED | 依赖项视为完成 |
| V2R-D | `8dcc696401d9` | REWORK_REQUIRED | 原分支追加修复提交 |
| V2R-X | `2039f8974408` | REWORK_REQUIRED | 原分支追加修复提交 |
| V2R-F | `b378d3bd8b17` | REWORK_REQUIRED | 原分支追加修复提交 |
| V2R-O1 | `853cf54b875d` | REWORK_REQUIRED | 原分支追加修复提交 |

## 2. V2R-Q1

判定：`ACCEPTED`。

验收证据：

- today 路由已使用 `Depends(get_db_path)`，测试通过 `dependency_overrides` 注入临时 DB。
- baseline capture 新增 `--db-path`，默认仍为 `runtime/stock_data.db`；identity 测试使用小型临时 SQLite。
- 定向 + OpenAPI/架构交叉复验：`22 passed, 1 skipped`，5.22 秒。
- Ruff：0；Mypy：0。
- 全量 worktree 诊断：`662 passed, 2 skipped, 3 failed`。三项失败来自既有测试对主仓目录名或主仓真实数据的隐式依赖：`test_universe_nonempty`、`test_build_panel_from_raw`、`test_default_db_path_points_into_project`；不在 Q1 diff/所有权内，登记为 Q2 集成质量债务，不将全量质量门描述为通过。

回滚：丢弃分支或 revert `8310b08aeb67`；无数据库副作用。

## 3. V2R-A

判定：`ACCEPTED`。

验收证据：

- facade 为 121 行；数据加载、预筛、单标的评估和编排已分离；根兼容 shim 保留。
- scanner golden、单/多 worker、Windows spawn、取消、进度写、升级和架构边界交叉复验：`59 passed`，9.09 秒。
- `scripts/check_architecture.py --strict`：通过。
- Ruff：0；Mypy：0。
- 未发现 ENTRY、评分阈值、A/B 池或订单语义改动。
- 非阻断观察：`orchestrator.py` 有 pandas `concat` FutureWarning，交给 Q2 清理，不影响本任务确定性结果。

回滚：丢弃分支或 revert `87a678be861d` 与 handoff 补记提交；无生产数据副作用。

## 4. V2R-D

判定：`REWORK_REQUIRED`。

已通过证据：定向 Pytest `23 passed`；Ruff 0；Mypy 0；当前 head 在副本上重跑 shadow parity 得到 20 标的 × 5 日期、600 字段比较、0 差异；真实数据 gate 如实为 FAIL，没有伪造 PASS。

必须修复：

1. `V2R-D-RW-001`（P0）：`shadow_parity` 在 0 标的、0 日期、0 比较时返回 `PASS`。管理者用空迁移库复现：`result=PASS, samples_checked=0, pairs_compared=0`。不足 20 标的、5 日期、100 样本或 600 字段比较必须返回 `INSUFFICIENT/FAIL`，不得以“无差异”视为通过；新增空库和不足样本测试。
2. `V2R-D-RW-002`（P1）：交接产物 `shadow_parity.json` 的 `code_sha=b6772c3...`，不是交付 head；修复后必须以新 head 重生报告，并使报告 code SHA、config hash、DB fingerprint 和产物 SHA-256 可核对。
3. `V2R-D-RW-003`（P1）：真实门禁在持仓和 `seed_codes` 同时为空时会跳过公司行为权限探测并继续。必须至少选择一个可验证标的；无法选择时返回 `INSUFFICIENT/FAIL`，不可检查 0 个代码后通过。

精确复验：

~~~powershell
& $py -m pytest tests/test_adjustment_asof.py tests/test_data_quality_v2.py tests/test_corporate_action_sync_v2.py -q -p no:cacheprovider
& $py -m ruff check ab_screener/data ab_screener/application/data_quality.py ab_screener/application/pit_backfill.py paper_trading/real_data_gate.py scripts/backfill_pit_v2.py
& $py -m mypy ab_screener/data ab_screener/application/data_quality.py ab_screener/application/pit_backfill.py paper_trading/real_data_gate.py
~~~

## 5. V2R-X

判定：`REWORK_REQUIRED`。

已通过证据：执行/血缘/dual-run/纸面 API 定向复验 `42 passed`；Ruff 0；Mypy 0；冻结场景费用已实现零分差。

必须修复：

1. `V2R-X-RW-001`（P0）：`_enforcement_enabled()` 已定义但没有被 `evaluate_order_risk()` 使用；`blocked` 和 `mode` 仍读取常量 `RISK_ENFORCE_DEFAULT=false`。管理者强制模拟 enforce 后，8 条违规仍得到 `blocked=false, mode=observe`。改为读取 resolved server flag，并增加 observe/enforce 双态测试。
2. `V2R-X-RW-002`（P0）：review/confirm 对统一风险入口的普通异常无条件吞掉；即使 enforce 已开启，风控不可用也会继续确认。observe 可返回明确的降级检查，enforce 必须 fail-closed 并使用结构化拒绝码；新增风险入口异常的双态测试。

精确复验：

~~~powershell
& $py -m pytest tests/test_execution_core_v2.py tests/test_execution_lineage.py tests/test_execution_dual_run_integration.py tests/test_risk_review_confirm_parity.py tests/test_order_risk_integration.py tests/test_paper_guidance.py tests/test_paper_api_acceptance.py -q -p no:cacheprovider
& $py -m ruff check ab_screener/domain/execution ab_screener/domain/risk paper_trading/engine.py paper_trading/orders.py paper_trading/risk_adapter.py paper_trading/guidance.py ab_screener/api/routers/legacy_paper.py
& $py -m mypy ab_screener/domain/execution ab_screener/domain/risk paper_trading/engine.py paper_trading/orders.py paper_trading/risk_adapter.py paper_trading/guidance.py ab_screener/api/routers/legacy_paper.py
~~~

## 6. V2R-F

判定：`REWORK_REQUIRED`。

已通过证据：TypeScript/Vite build 成功；4 个页面和路由已创建；提交未包含 dist；本地业务 flag 的 URL/localStorage 覆盖已移除。

必须修复：

1. `V2R-F-RW-001`（P0）：`package.json` 新增测试依赖但 `package-lock.json` 未更新；Vitest 实跑因缺失 `http-proxy-agent` 失败，0 个测试被收集。同步 lockfile，并以干净 `npm ci` 后的 `npm run test` 为准。
2. `V2R-F-RW-002`（P0）：Playwright 结果为 `3 passed, 1 failed`；配置没有 `webServer`，实际命中了端口 3001 上既有应用，不能证明当前分支。让 E2E 自行启动当前 branch 的 Vite/preview，禁止复用未知旧服务，再覆盖刷新、失焦恢复、390px 和键盘主操作。
3. `V2R-F-RW-003`（P1）：引导模式可信验证仍发送 `step: 10`，合同要求 `max_codes=600, step=5, mode=grid, 自动窗口`。修正并增加请求体断言。
4. `V2R-F-RW-004`（P1）：前端 `SystemHealth` 使用 flat 字段（`db_size_mb/wal_size_mb/disk_free_gb/errors`），后端返回 nested `database/disk/issues/build_version`；System/Monitor 会显示破折号并丢失真实深检状态。按 OpenAPI/实际响应更新 typed client、页面和契约 fixture；备份大小使用 `size_bytes`。四页补足加载、空、错误、证据不足和正常状态测试。

精确复验：

~~~powershell
npm --prefix web/frontend ci
npm --prefix web/frontend run test
npm --prefix web/frontend run build
npm --prefix web/frontend run test:e2e
~~~

## 7. V2R-O1

判定：`REWORK_REQUIRED`。

已通过证据：定向 Pytest `8 passed`；Ruff 0；Mypy 0；对 16GB 生产库的只读快速健康实测约 10.1ms，未执行完整 integrity check；当前备份状态诚实为 2 份、不满足 7 份门禁。

必须修复：

1. `V2R-O1-RW-001`（P0）：计划规定的 headless 命令 `restore_backup.ps1 -BackupRoot E:\ab-backups -DryRun` 实际 exit 1，原因是 `RestoreTo` 被声明为 Mandatory。DryRun 未给目标时必须推导并打印安全临时目标；真实恢复仍只允许新临时目录，测试必须执行合同中的原命令。
2. `V2R-O1-RW-002`（P0）：OpenAPI 仍公开 `backup_root` 查询参数，且显式参数优先于环境变量；这允许 HTTP 调用者覆盖生产备份根，违反合同。生产路由只能读取 `AB_BACKUP_ROOT`；测试注入使用依赖覆盖或纯函数，不暴露 HTTP 参数。
3. `V2R-O1-RW-003`（P1）：健康测试只断言响应里没有 `integrity` 字段，没有按计划 monkeypatch SQL 调用。增加执行捕获：出现 `PRAGMA integrity_check` 或 `quick_check` 立即失败，并保留真实大库 `<500ms` 的性能证据。

精确复验：

~~~powershell
& $py -m pytest tests/test_backup_restore.py tests/test_system_health_fast.py tests/test_restore_backup_contract.py -q -p no:cacheprovider
& $py -m ruff check ab_screener/operations ab_screener/api/routers/system.py scripts/check_db_integrity.py
& $py -m mypy ab_screener/operations ab_screener/api/routers/system.py scripts/check_db_integrity.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/restore_backup.ps1 -BackupRoot E:\ab-backups -DryRun
~~~

## 8. 当前系统事实边界

本次 Wave 1 工程验收不改变生产/研究结论。权威证据仍显示：机构分 86，`anti_overfit_pass=false`、`anti_overfit_strict_pass=false`；商业复核 88，低于 94 通过线；持仓同步为 `stale_local_cache`。因此整体状态继续为 `BLOCKED`，不得宣称 9 分、机构级完成或真实交易就绪；`LIVE_TRADING_ENABLED` 必须继续为 false。

## 9. 返工提交规则

- 四个任务在原 worktree/原分支追加 commit，不 force-push、不重写已审提交。
- 只修改各自 owned paths；不得修改共享入口、生产 flags、任务总状态或 `web/frontend/dist`。
- handoff 更新新 head、修改前失败和修改后通过原始证据。
- 管理者二验仅在四项全部 ACCEPTED 后释放 Wave 2。
