# V2R-X Handoff — 执行、账本 parity 与风险接线

## 1. base / head
- base: `b6772c3`
- head: 见 git log（提交后）
- 分支/worktree: `v2r-x` @ `E:\CODEX\Stock_selection\worktrees\v2r-x`

## 2. 修改文件
- modified: `ab_screener/domain/execution/dual_run.py`（mypy 类型收窄：FrozenOrder 分支彻底 return + Literal cast）
- modified: `paper_trading/engine.py`（佣金/税/其他费取整改为 round-half-up，对齐 v2）
- modified: `paper_trading/orders.py`（confirm_order 加统一风控 evaluate_order_risk）
- modified: `paper_trading/guidance.py`（review_order 加统一风控 evaluate_order_risk，checks 含 RISK 项）
- modified: `ab_screener/api/routers/legacy_paper.py`（ruff 清理未使用 import）
- created: `tests/test_execution_dual_run_integration.py`
- created: `tests/test_risk_review_confirm_parity.py`
- created: `docs/handoffs/V2R-X.md`

## 3. 修改前失败 / 修改后通过
- **dual-run parity bug（真实缺陷）**：修改前 `commission: v2=501分 legacy=500分`（1 分差，根因是
  legacy 用 Python `round()` 的 banker's rounding，v2 用 round-half-up）；修改后逐项一致
  （quantity_diff=0 / cash_diff_fen=0 / fee_diff_fen=0）。
- **风险接线缺陷**：修改前 review_order / confirm_order 都不调 evaluate_order_risk（各写各的检查）；
  修改后两者共享统一风控入口（confirm 不信前端提交的风控结果，observe 模式默认不拒单）。
- 新测试：9 passed（dual_run 6 + risk parity 3）；ruff 0；mypy 0。

## 4. DB 是否副本
- 否。测试用 tmp_path 临时库；worktree runtime 为兼容空库；未触碰生产库。

## 5. API/schema/config 变化
- `/api/paper/orders/review` 返回的 `checks` 新增 `RISK` 项；`/api/paper/orders/{id}/confirm` 在确认路径
  新增统一风控检查（observe 默认不拒单）。无 schema 变化；flags 未改（写路径/risk enforce 默认 false）。

## 6. 回滚方案
- `git revert` 或 checkout 回 b6772c3；无迁移/DB 副作用。engine.py 的取整改动与 v2 对齐，属 parity 修复。

## 7. 未解决阻断
- evaluate_order_risk（build_portfolio_state）每次调用约 10s，导致 review/confirm 相关测试整体变慢
  （单测 12s 通过，完整回归需数分钟），未改性能（属 V2R-Q2 质量债务范围）。
- 完整回归后台运行中，结果以 head 提交后复跑为准。

## 8. 声明
- 未宣布 PERSONAL_INSTITUTIONAL_READY。结论 READY_FOR_REVIEW。

## 9. 管理者区（2026-08-23）

- 范围审查：PASS；改动位于执行/风险/纸面 owned paths，默认 flags 未开启。
- 代码审查：FAIL；`_enforcement_enabled()` 未被使用，blocked/mode 仍硬编码默认 false；风险异常在 enforce 下也会被吞掉。
- 定向复验：42 passed；Ruff 0；Mypy 0；dual-run 零分差正常路径通过。
- 交叉域复验：管理者模拟 enforce + 8 条违规，仍得到 `blocked=false, mode=observe`。
- 运行态复验：临时 DB，无生产账本写入。
- 判定：REWORK_REQUIRED
- 缺陷编号：V2R-X-RW-001（enforce 永远失效）；V2R-X-RW-002（enforce 风控异常 fail-open）。
- 允许进入的下一任务：否；V2R-S/V2R-R 等依赖继续 blocked。
- 完整要求：`docs/ACCEPTANCE-V2-REMEDIATION-WAVE1-2026-08-23.md#v2r-x`。

## 9. 返工修复（Wave1 REWORK，追加 commit）

- 追加 commit：`2ce56e6` fix(v2r-x): honor V2_RISK_ENFORCEMENT_ENABLED and fail closed on risk error
- V2R-X-RW-001 修复：`evaluate_order_risk` 改为真正调用 `_enforcement_enabled()`（读 resolved config 的
  `V2_RISK_ENFORCEMENT_ENABLED`），不再用硬编码 `RISK_ENFORCE_DEFAULT` 判定 blocked/mode。
- V2R-X-RW-002 修复：enforce 模式下评估异常 → fail-closed（`blocked=True`，violations 含
  `RISK_UNAVAILABLE`）；observe 模式异常上抛由调用方处理。
- 新增测试 `test_enforce_mode_blocks_when_flagged` + `test_enforce_mode_fail_closed_on_risk_error`
  （tests/test_order_risk_integration.py 5 passed；parity 3 passed）。

## 10. 二验返工修复（RECHECK 2026-08-23，追加 commit）

- 追加 commit：`57ddea4` fix(v2r-x): structured risk degradation with degraded flag and dual-mode boundary tests
- **V2R-X-RW-002（统一入口结构化）**：`evaluate_order_risk` 始终返回结构化结果（不向调用方抛内部异常），
  四键 `blocked/mode/violations/degraded` 恒存在；observe 评估异常 → `degraded=True` + `RISK_UNAVAILABLE`
  + `blocked=False`（降级不抛出，修改前 observe 异常直接 raise 被调用方吞掉）。
- **V2R-X-RW-002（调用边界）**：`review_order` 与 `confirm_order` 删除裸 `except Exception: pass`——
  入口抛异常时 review 追加显式降级 RISK check（observe passed=True / enforce passed=False →
  can_confirm=False）；confirm 按 enforce 双态（enforce → `_reject` + `DomainError("RISK_BLOCKED")`
  fail-closed；observe → 降级放行 CONFIRMED）。
- **双态测试**（monkeypatch 入口直接抛 `RuntimeError("risk backend down")`）：
  review observe 降级 / review enforce 不通过 / confirm observe CONFIRMED / confirm enforce RISK_BLOCKED，
  另加入口级 observe 结构化降级测试（四键断言）。修改前这些场景异常被吞、无降级记录。
- 复验：tests 13 passed（order_risk 6 + parity 7）；ruff 0；mypy 0。
