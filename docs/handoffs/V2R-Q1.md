# V2R-Q1 Handoff — 正确性回归与快速可重复基线

## 1. base / head
- base: `b6772c3`（固定基线）
- head: 见 git log（提交后）
- 分支/worktree: `E:\CODEX\Stock_selection\worktrees\v2r-q1`（分支 `v2r-q1`）

## 2. 修改文件
- modified: `ab_screener/api/routers/legacy_misc.py`（today API 依赖注入）
- modified: `tests/test_today_guide.py`（dependency_overrides）
- modified: `scripts/capture_v2_baseline.py`（新增 `--db-path`）
- modified: `tests/test_v2_baseline_manifest.py`（临时库 + read_bytes hash + 容忍清理失败）
- created: `tests/test_build_version.py`
- created: `docs/handoffs/V2R-Q1.md`（本文件）

## 3. 修改前失败 / 修改后通过证据
- `test_today_api_returns_the_server_derived_action`：修改前 FAILED（today 路由捕获 `legacy_state._DB`，
  monkeypatch `backend._DB` 不影响 legacy_misc 命名空间 → 读生产库返回 DAILY_COMPLETE 而非 RUN_SCAN）；
  修改后 6 passed（依赖 `Depends(get_db_path)` + `dependency_overrides`）。
- `test_identity_stable_across_runs`：修改前超时（capture_v2_baseline 扫 16GB 生产库 >120s）；
  修改后 2.57s 通过（`--db-path` 小型临时库，两次 identity 一致）。
- 定向复验：13 passed, 1 skipped（skip = `test_blocked_when_dirty_or_inconsistent`，WORKTREE_DIRTY 预期）
- ruff：All checks passed；mypy：Success

## 4. DB 是否为副本
- 否。测试全程使用 tmp_path 临时库 / worktree 内置空库（LocalStore 迁移建库，无生产数据）。
- worktree `runtime/stock_data.db` 为 b6772c3 代码自建兼容空库（v1+v2 迁移），未触碰主仓库生产库。

## 5. API/schema/config 变化
- API 行为不变（`GET /api/today` 返回结构不变）；today 路由从直接捕获 `_DB` 改为 `Depends(get_db_path)`。
- 无 schema/config 变化。`capture_v2_baseline.py` 新增 `--db-path`（默认仍 `runtime/stock_data.db`）。

## 6. 回滚方案
- `git revert` 或 checkout 回 `b6772c3` 即可（改动仅 5 个文件，无迁移/DB 副作用）。

## 7. 未解决阻断
- worktree 环境的 `runtime/v2/baseline_manifest.json` 为验证用生成（pytest 结果复用主仓库基线）；
  主仓库（有真实基线）无需此操作。
- WorkBuddy sandbox 的 safe-delete 在回收站不可用时 fail-closed，测试清理已容忍（try/except OSError）。

## 8. 声明
- 未宣布 PERSONAL_INSTITUTIONAL_READY。
- 结论：READY_FOR_REVIEW（等管理者验收）。
