# Breakout v2 最终集成与验收执行计划

## 目标

以 `v2r-wave2-integration` 为干净基线，只在独立 worktree
`E:\CODEX\Stock_selection\worktrees\v2r-final-integration` 中完成：

1. 独立复验并集成 `V2R-O2`；
2. 完成 `V2R-Q2` 全仓质量债务收口；
3. 完成 `V2R-G` 共享入口、服务端 flags、readiness 与统一前端构建；
4. 由当前管理者执行 `V2R-P8` 七闸门总验收；
5. 仅在证据支持时更新任务状态，真实时间不足时保持 `BLOCKED`。

## 固定边界

- 唯一项目：`E:\CODEX\Stock_selection\accumulation_breakout`。
- 目标运行端口：`8001`；不修改或复用 AETF 的 `8000`。
- 权威 Python：`E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`。
- `LIVE_TRADING_ENABLED=false`，不得新增券商实盘能力。
- 不修改生产数据库；故障注入、迁移和恢复只使用临时库或已验证副本。
- 不伪造五个真实交易日 soak，不用历史回填替代真实时间。
- 不覆盖根 worktree 的用户改动；最终切换前先比较并人工合并。

## 影响文件

### O2 集成与复验

- `ab_screener/operations/{dag,scheduler,alerts}.py`
- `ab_screener/application/audit_service.py`
- `ab_screener/data/scheduler_repository.py`
- `scripts/soak_monitor_v2.py`
- `tests/test_{daily_dag,daily_dag_closed_loop,dag_order,fault_injection_scheduler,scheduler_lease,audit_hash_chain,soak_monitor_v2}.py`
- `docs/handoffs/V2R-O2.md`

### Q2 质量收口

- 只修改质量命令实际报告的 import、typing、测试标记或确定性缺陷文件。
- `pyproject.toml` 仅在测试 marker/既有门禁确有缺口时修改。
- 禁止扩大 Ruff/Mypy ignore、删除测试或改变策略阈值。

### G 共享入口

- `web/backend_app.py`
- `ab_screener/api/app_factory.py`
- `ab_screener/api/routers/readiness.py`
- `ab_screener/application/platform_config.py`
- `ab_screener/domain/readiness.py`
- `configs/platform_v2.yaml`
- `web/frontend/src/api/platform.ts`
- `web/frontend/src/layout/Sidebar.tsx`
- `web/frontend/dist/**`
- `tests/test_{readiness_v2,openapi_contract_v2,platform_config}.py`
- `docs/handoffs/V2R-G.md`

### P8 状态与证据

- `docs/ACCEPTANCE-V2-REMEDIATION-FINAL-2026-08-27.md`
- `tasks/v2_remediation_board_20260823.yaml`
- `tasks/backlog.yaml`
- `tasks/implementation_state.yaml`
- `docs/STATUS.md` 与 `docs/RESEARCH-ROADMAP.md` 仅在不覆盖用户内容的前提下合并。

## 执行顺序与出口

1. O2：文件所有权、提交拓扑、定向测试、fault injection、全量回归和副本证据复核；通过后合并。
2. Q2：记录原始失败集合，机械修复与语义修复分离；Ruff、Mypy、Pytest、performance、fault injection、前端测试/构建全部退出 0。
3. G：readiness fail-closed；服务端读取真实证据；关闭能力返回结构化错误；统一构建并核对静态资产。
4. P8：核对代码/config/DB/前端/研究证据身份，复跑 D/R/S/P/L/O/G 与运行态浏览器验收。
5. 最终状态只允许 `BLOCKED`、`ENGINEERING_READY_RESEARCH_BLOCKED` 或 `PERSONAL_INSTITUTIONAL_READY`；研究失败或真实时间不足必须诚实保留阻断。

## 回滚

- 每个阶段独立提交，使用 `git revert <commit>` 回滚。
- 不删除历史账本、信号、失败研究、审计或 soak 证据。
- 当前根 worktree 保持不动；若最终集成不通过，不切换 8001 服务。
