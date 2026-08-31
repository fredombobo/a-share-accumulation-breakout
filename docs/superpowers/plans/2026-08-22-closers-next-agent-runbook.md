# 收口下一刀 — 实现 Agent 手册（2026-08-22）

先读：[索引](2026-08-22-closers-next-index.md) → [计划](2026-08-22-closers-next-plan.md) → [验收](2026-08-22-closers-next-acceptance.md) → `docs/ACCEPTANCE-CLOSERS-2026-08-22.md` → 根 `AGENTS.md`。

一次只领 **N0 / N2 / N3** 之一。N1 跟在 N0 后面由同一工程 Agent 或用户做。

## 工作树

```text
E:\CODEX\Stock_selection\accumulation_breakout
https://github.com/fredombobo/a-share-accumulation-breakout
```

- N0：在现有 `closers-g2-split` 上继续，或从该分支开 `closers-e2-fix`。不要用 `codex/` 前缀。  
- N2/N3：不要改 `legacy_scan.py` / `legacy_lab.py` / `backend_app.py`。  
- 不覆盖 `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md`。  
- 不改 `configs/platform_v2.yaml` 生产旗标。

## Git

```powershell
$git = "C:\Users\13818\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe"
Set-Location E:\CODEX\Stock_selection\accumulation_breakout
& $git status -sb
& $git fetch origin --prune
& $git log --oneline -5
```

提交前缀：`fix(closers-e2):` / `feat(closers-d):` / `docs(closers):`

## Python

优先：

```powershell
$Py = "E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe"
& $Py -c "import sys; print(sys.version)"
```

必须看到 3.12.x。若启动器坏了：

```powershell
$Py = "E:\C_Drive_Moved_2026-06-03\AppData_Junctions\AppData\Local\Programs\Python\Python312\python.exe"
$Root = "E:\CODEX\Stock_selection\accumulation_breakout"
$env:PYTHONPATH = "$Root;$Root\.venv312\Lib\site-packages"
```

清代理：`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 及小写四套都 `$null`。  
禁止用 `C:\Python314\python.exe` 当证据。

## N0 最小命令

```powershell
& $Py -m ruff check ab_screener\api\routers\legacy_scan.py ab_screener\api\routers\legacy_lab.py --select F821
& $Py scripts\check_architecture.py --strict
& $Py -m pytest tests\test_closers_e2_split_regressions.py tests\test_scan_progress_io.py tests\test_lab_task_recovery.py tests\test_openapi_contract_v2.py tests\test_architecture_boundaries.py tests\test_entry_definition_v1_golden.py -q --tb=short
```

## Handoff 模板

写到 `docs/handoffs/CLOSERS-N<n>-<DATE>.md`：

```markdown
# CLOSERS-N<n> Handoff

## 身份
- Agent:
- 基线 commit:
- 交付 commit:
- 契约版本: CLOSERS-NEXT-2026-08-22
- 刀: N0 | N1 | N2 | N3

## 环境
- Python:
- sys.version:
- git:

## 完成范围
- [ ]

## 明确未完成
- [ ]

## 修改文件
- added / modified:
- 是否改 platform_v2.yaml: no
- 是否改 STATUS.md: no

## 测试证据
- 精确命令、退出码、passed 数:

## 产物证据
- 是否写生产 DB PIT: no
- daily MAX:
- 旗标打印:

## 闸门自测
- 本刀验收 ID:

## 结论
- READY_FOR_REVIEW | BLOCKED
- 建议总状态: BLOCKED
```

## 检查 Agent

复跑验收矩阵，写 `docs/ACCEPTANCE-CLOSERS-NEXT-YYYY-MM-DD.md`。  
总评：`ACCEPTED_ENGINEERING_SLICE` / `REJECTED` / `BLOCKED`。

---

## 可粘贴开工提示词

### 工程 Agent（N0，先做这个）

```text
你是 accumulation_breakout 的实现 Agent。只做 N0 E2-FIX。
根：E:\CODEX\Stock_selection\accumulation_breakout
必读：
docs/superpowers/plans/2026-08-22-closers-next-index.md
以及同目录 plan / acceptance / agent-runbook。
独立检查：docs/ACCEPTANCE-CLOSERS-2026-08-22.md

只修两处 NameError，并补 tests/test_closers_e2_split_regressions.py（≥3 用例）：
1) ab_screener/api/routers/legacy_scan.py 从 legacy_state 补导入 _BUILD_VERSION、_OVERVIEW_CACHE；完成路径清缓存抽成 _clear_overview_cache。
2) ab_screener/api/routers/legacy_lab.py 顶层 import json。
可删 backend_app.py 未使用导入。不要改入场 V1、不要改旗标、不要覆盖 STATUS.md、不要开 LIVE。
分支名不要用 codex/ 前缀。
完成后写 docs/handoffs/CLOSERS-N0-E2-FIX-YYYY-MM-DD.md，结论只能 READY_FOR_REVIEW 或 BLOCKED。
```

### 数据 Agent（N2，可与 N0 并行）

```text
你是 accumulation_breakout 的实现 Agent。只做 N2 Wave D。
根：E:\CODEX\Stock_selection\accumulation_breakout
必读：docs/superpowers/plans/2026-08-22-closers-next-index.md 及同目录 plan/acceptance/agent-runbook。
硬约束：LIVE false；PIT --run 只打绝对路径副本，禁止 runtime/stock_data.db；不开 V2_PIT_READ_ENABLED；
不改 STATUS.md；不改 legacy_scan.py / legacy_lab.py / backend_app.py。
先 sync_daily，再 stock_basic / cyq 续跑，fina 与 holder 按 ts_code 分区；权限不够就书面 INSUFFICIENT，禁止假数据。
coverage 落到 runtime/v2/pit_coverage_<stamp>.json。
完成后写 docs/handoffs/CLOSERS-N2-D-YYYY-MM-DD.md。
```

### 运维 Agent（N3）

```text
你是 accumulation_breakout 的实现 Agent。只做 N3 Wave O-min。
根：E:\CODEX\Stock_selection\accumulation_breakout
必读：docs/superpowers/plans/2026-08-22-closers-next-index.md 与 docs/BACKUP-RESTORE-RUNBOOK-V2.md。
没有用户提供的 AB_BACKUP_ROOT（绝对路径、不在 runtime\ 下）则停止并在 handoff 说明。
做 1 份校验备份 + restore DryRun（实跑须用户确认，禁止覆盖生产库）。
不要开启 DAILY_SCHEDULER_ENABLED。不要把 pt_cycle 当 soak。
完成后写 docs/handoffs/CLOSERS-N3-O-YYYY-MM-DD.md。
```
