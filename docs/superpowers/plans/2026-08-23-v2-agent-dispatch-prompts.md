# v2.0 纠错任务派发文本

以下每一节可原样交给一个实现 Agent。Agent 必须在收到的独立 worktree 中工作，不得切换到共享主工作区。

## 通用前缀（每次派发都附上）

项目：E:\CODEX\Stock_selection\accumulation_breakout

先完整阅读：

1. 当前 worktree 内 AGENTS.md；
2. docs/superpowers/plans/2026-08-23-v2-remediation-manager-plan.md；
3. tasks/v2_remediation_board_20260823.yaml 中你的任务；
4. docs/handoffs/V2-REMEDIATION-AGENT-HANDOFF-TEMPLATE.md。

约束：

- 只修改 owned_paths，禁止触碰 protected/shared paths。
- TDD：先展示失败测试，再实现，再运行定向测试。
- 不修改真实交易硬门；LIVE_TRADING_ENABLED 必须为 false。
- 不删除失败测试、不扩大 ignore、不覆盖生产 DB/备份/账本。
- 完成后提交一个或多个小 commit，并写固定 handoff 文件。
- 回复必须给 base SHA、head SHA、changed files、测试原始摘要、回滚和未决问题。
- 不得宣布 PERSONAL_INSTITUTIONAL_READY。

## 派发 A：V2R-Q1

你的唯一任务是 V2R-Q1“正确性回归与快速可重复基线”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-q1

严格执行主计划 Task 1。重点修复 today API 数据库依赖注入和 baseline identity 测试误扫 16GB 生产库；不能靠增加 timeout，不能降低生产深检。交付 docs/handoffs/V2R-Q1.md。

## 派发 B：V2R-A

你的唯一任务是 V2R-A“扫描内核拆分与确定性回归”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-a

严格执行主计划 Task 1A。只拆职责，不改变 ENTRY、评分、阈值、A/B 池或 golden 结果；保留根兼容 shim 和 Windows spawn/取消语义。交付 docs/handoffs/V2R-A.md。

## 派发 C：V2R-D

你的唯一任务是 V2R-D“PIT、公司行为与当前身份数据门禁”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-d

严格执行主计划 Task 2。所有迁移/backfill 先在 E:\ab-maintenance\v2r-d\stock_data_copy.db 证明；不得修改生产库，不得打开 V2_PIT_READ_ENABLED，不得修改 tushare_init.py。交付 docs/handoffs/V2R-D.md。

## 派发 D：V2R-X

你的唯一任务是 V2R-X“统一执行核心、账本 parity 与风险接线”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-x

严格执行主计划 Task 3。review/confirm 必须共享后端风险计算；dual-run 只能比较，默认不能写第二份账；现金和费用保持整数分。不得打开 execution write 或 risk enforce。交付 docs/handoffs/V2R-X.md。

## 派发 E：V2R-F

你的唯一任务是 V2R-F“v2 控制台缺页、前端测试和无障碍 E2E”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-f

严格执行主计划 Task 4。实现 Monitor、Review、System、Compare，补 Vitest/Playwright/390px/键盘/恢复测试；修可信验证默认 600 股、步长 5。禁止提交 web/frontend/dist，禁止在前端计算账务或绕过服务端 flags。交付 docs/handoffs/V2R-F.md。

## 派发 F：V2R-O1

你的唯一任务是 V2R-O1“快速健康检查、备份接线与严格恢复演练”。

工作目录：E:\CODEX\Stock_selection\worktrees\v2r-o1

严格执行主计划 Task 5。GET 健康接口不能执行完整 integrity_check；深检必须离线产证。AB_BACKUP_ROOT 未设置需明确失败。不能删除 E:\ab-backups 现有文件，不能打开 scheduler。交付 docs/handoffs/V2R-O1.md。

## 派发 G：V2R-S（收到管理者解锁后才执行）

你的唯一任务是 V2R-S“六形态扫描、不可变信号、成交/outcome 生产接线”。

工作目录由管理者在 Wave 1 接受后提供。

严格执行主计划 Task 6。EXPERIMENTAL 不得进入 A 池；ENTERED 只能由 fill 产生；重扫不覆盖历史。禁止修改 app_factory/config。交付 docs/handoffs/V2R-S.md。

## 派发 H：V2R-O2（收到管理者解锁后才执行）

你的唯一任务是 V2R-O2“持久 EOD DAG、故障恢复、审计链与五日 soak 启动”。

工作目录由管理者在 V2R-O1、V2R-D、V2R-X、V2R-S 接受后重新创建，分支名固定为 agent/v2r-o2；不得在旧 O1 worktree 上直接叠加。

严格执行主计划 Task 7。先在数据库副本故障注入与重放；生产 scheduler flag 仍关闭。真实五日 soak 只能等待真实交易日，不得补写。交付 docs/handoffs/V2R-O2.md。

## 派发 I：V2R-R（收到管理者解锁后才执行）

你的唯一任务是 V2R-R“600 股、步长 5 的完整可信研究证据”。

工作目录由管理者在 Wave 1 接受后提供。

严格执行主计划 Task 8。结果允许 FAIL/INSUFFICIENT；不得调门槛、删失败窗或把 PASS 接到 A 池。若发现统计实现缺陷，先写失败 fixture，单独 commit 后从头重跑。交付 docs/handoffs/V2R-R.md。

## 派发 J：V2R-N（收到管理者解锁后才执行）

你的唯一任务是 V2R-N“国家队/机构资金等信息增强只读覆盖层”。

工作目录由管理者在 Wave 1 接受后提供。

严格执行主计划 Task 9 和两份 2026-08-22 NTM 计划。覆盖层必须有 PIT 五元组，且启用前后 A/B 池、仓位和订单逐项一致。无权限时输出 INSUFFICIENT，不得伪造数据。交付 docs/handoffs/V2R-N.md。

## 派发 K：V2R-Q2（所有功能合并后）

你的唯一任务是 V2R-Q2“集成后 Ruff/Mypy/测试债务清零”。

只处理管理者列出的最新失败集合和临时授权文件。不得修改策略、交易、风险或数据语义，不得扩大 ignore/exclude。完成 Ruff 0、Mypy 0、Pytest 0 failed、前端 test/build、performance 和 fault_injection 实际收集。交付 docs/handoffs/V2R-Q2.md。

## 派发 L：V2R-G（最后一个实现 Agent）

你的唯一任务是 V2R-G“共享入口、服务端 flags、readiness、台账建议和最终构建收口”。

严格执行主计划 Task 11。修 readiness 优先级和状态名，添加真实生产调用点，让 flags 在服务端生效，最终只构建一次 dist。不得修改受保护的任务/STATUS/ROADMAP 文件；只在 handoff 提建议。交付 docs/handoffs/V2R-G.md。

## 不派发：V2R-P8

V2R-P8 永远由管理者执行。任何实现 Agent 主动领取、改最终状态或宣布就绪，交付直接 REJECTED。
