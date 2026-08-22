# CLOSERS-N0 E2-FIX Handoff

## 身份
- Agent: 实现 Agent（WorkBuddy · SeniorDeveloper）
- 基线 commit: `a86b03e`（E3 完成点）
- 交付 commit: `b1629b3`
- 契约版本: CLOSERS-NEXT-2026-08-22
- 波次: N0（E2-FIX）

## 环境
- Python 可执行文件: `.venv312\Scripts\python.exe`（sys.version 3.12.10）
- git: 分支 `closers-g2-split`（单层分支名）

## 完成范围
- [x] 缺陷1：`legacy_scan.py` 补 `_BUILD_VERSION`、`_OVERVIEW_CACHE` 导入（来自 `legacy_state`），
      并抽出 `_clear_overview_cache()` 小函数，扫描完成路径改为调用它
- [x] 缺陷2：`legacy_lab.py` 顶层补 `import json`（Lab 报告 JSON 下载 NameError）
- [x] 新测试 `tests/test_closers_e2_split_regressions.py`（3 用例：扫描模块绑定 / 清缓存可执行 / Lab JSON 下载）
- [x] 宿主清理：`web/backend_app.py` 顶层 32 处未使用 import 删除（ruff F401 --fix），
      只保留装配真正用到的符号（`_DB`/`_LOGGER`/`_store` + `_paper_enabled` 单独 import）

## 明确未完成
- [ ] N1（PR 合 main，需用户确认推送远程）
- [ ] N2（Wave D 数据，需 Token + 维护窗口）
- [ ] N3（Wave O-min，需用户给 AB_BACKUP_ROOT）

## 修改文件
- modified:
  - `ab_screener/api/routers/legacy_scan.py`（补 import + `_clear_overview_cache`）
  - `ab_screener/api/routers/legacy_lab.py`（顶层 `import json`）
  - `web/backend_app.py`（未使用 import 清理，308 → 276 行）
- added:
  - `tests/test_closers_e2_split_regressions.py`
- 是否改 platform_v2.yaml: no
- 是否改 STATUS.md: no
- shared hotspot: legacy_scan / legacy_lab / backend_app（N0 冲突文件）

## 测试证据
- `pytest tests/test_closers_e2_split_regressions.py -q` → 3 passed
- 定向回归（新测试 + scan_progress_io + lab_task_recovery + openapi_contract_v2
  + architecture_boundaries + entry_definition_v1_golden）→ 26 passed
- `ruff check ab_screener/api/routers/legacy_scan.py ab_screener/api/routers/legacy_lab.py --select F821` → All checks passed
- `check_architecture.py --strict` → exit 0
- 全量 `pytest tests/ -q -k "not browser"` → **662 passed, 1 failed**
  （唯一 failed = `test_v2_baseline_manifest.py::test_identity_stable_across_runs`，
  为既有 baseline 采集超时，plan N0-9 明确允许，非本刀引入）

## 产物证据
- 是否使用真实 Token: no
- 是否修改 runtime 账本: no
- 是否写生产 DB PIT: no
- daily MAX: 20260818（未变）
- 旗标打印: `V2_PIT_READ_ENABLED=False` `LIVE_TRADING_ENABLED=False`

## 闸门自测（实现侧，非正式验收）
- N0-1 legacy_scan import 含 _BUILD_VERSION/_OVERVIEW_CACHE: PASS
- N0-2 legacy_lab 顶层 import json: PASS
- N0-3 新测试 ≥3 用例: PASS（3 用例）
- N0-4 新测试 exit 0: PASS（3 passed）
- N0-5 定向回归: PASS（26 passed）
- N0-6 ruff F821=0: PASS
- N0-7 架构 --strict: PASS
- N0-8 旗标: PASS（PIT 读/LIVE 均 false）

## 结论
- READY_FOR_REVIEW
- 建议总状态: BLOCKED（D/O/R 闸门仍 INSUFFICIENT，非本刀范围）
