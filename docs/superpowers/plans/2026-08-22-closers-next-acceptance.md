# 收口下一刀 — 验收矩阵（2026-08-22）

> 检查 Agent 必须亲自复跑。实现方 handoff 不是通过证据。  
> 总评只允许：`ACCEPTED_ENGINEERING_SLICE` / `REJECTED` / `BLOCKED`。  
> 禁止输出 `PERSONAL_INSTITUTIONAL_READY`。

## 一票否决

- 打开 LIVE 或任何 Wave F 生产旗标  
- 改 V1 入场  
- 情报/astock 写入 A 池或纸面  
- PIT `--run` 直接打生产库且无用户维护窗口记录  
- 用 Python 3.14 当唯一 pytest 证据  
- 覆盖 STATUS / RESEARCH-ROADMAP  
- 研究 FAIL 改写成 PASS  
- N0 只改 import、不交新测试  

## 环境

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
$Py = ".\.venv312\Scripts\python.exe"
# 若启动失败：
# $Py = "E:\C_Drive_Moved_2026-06-03\AppData_Junctions\AppData\Local\Programs\Python\Python312\python.exe"
# $env:PYTHONPATH = "$pwd;$pwd\.venv312\Lib\site-packages"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue   # 仅在用 venv 启动器时
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
& $Py -c "import sys; print(sys.version)"   # 必须 3.12.x
```

## N0 E2-FIX

| ID | 检查 | 通过 |
|---|---|---|
| N0-1 | `legacy_scan.py` import 含 `_BUILD_VERSION` 与 `_OVERVIEW_CACHE` | 源码可见；来自 `legacy_state` |
| N0-2 | `legacy_lab.py` 顶层 `import json` | 源码可见 |
| N0-3 | 新测试文件存在且 ≥3 用例 | 扫描绑定 / 清缓存 / lab JSON |
| N0-4 | `& $Py -m pytest tests/test_closers_e2_split_regressions.py -q` | exit 0 |
| N0-5 | 定向回归：`test_scan_progress_io` `test_lab_task_recovery` `test_openapi_contract_v2` `test_architecture_boundaries` `test_entry_definition_v1_golden` | 全绿 |
| N0-6 | `& $Py -m ruff check ab_screener/api/routers/legacy_scan.py ab_screener/api/routers/legacy_lab.py --select F821` | exit 0，0 error |
| N0-7 | `& $Py scripts\check_architecture.py --strict` | exit 0 |
| N0-8 | 旗标 | `V2_PIT_READ_ENABLED=false` `LIVE_TRADING_ENABLED=false` |
| N0-9 | 全量 `pytest tests/ -q -k "not browser"` | 除 `test_identity_stable_across_runs` 超时外无新失败 |

N0-9 的基线超时 **不算** N0 失败，但必须在 handoff 写明 failed 测试全名。出现第二个失败 = REJECTED。

## N1 PR

| ID | 通过 |
|---|---|
| N1-1 | `origin/main` 含 N0 commit，或 PR 已开且检查 Agent 能指出 URL |
| N1-2 | PR 未宣称 READY |
| N1-3 | 若 CI 跑了：architecture `--strict` 绿；ruff 若红仅限 N0 未改的旧文件并已列出 |

无远程权限 → N1=`BLOCKED`（等待用户），不连坐 N0。

## N2 数据

| ID | 命令 / 检查 | 通过 |
|---|---|---|
| D1 | `MAX(trade_date) FROM daily` | 等于源端最近已收盘日 |
| D2 | backfill `--db` | 绝对路径 **副本**，preflight PASS |
| D3 | `stock_basic_history` COUNT | >0 或书面 INSUFFICIENT |
| D4 | cyq checkpoints | 补完或书面剩余 |
| D5 | fina / holder | 完成或 INSUFFICIENT，禁止假行 |
| D6 | `runtime/v2/pit_coverage_<stamp>.json` | 含 partitions/done/rows |
| D7 | yaml | `V2_PIT_READ_ENABLED: false` |
| D8 | 生产库 | 无用户确认则未被 `--run` 直接回填 |

## N3 运维 O-min

| ID | 通过 |
|---|---|
| O0 | `AB_BACKUP_ROOT` 绝对、可写、不在 `runtime\` |
| O1 | 1 份校验备份存在 |
| O2 | restore DryRun 过；实跑则 RTO≤1800s 且未覆盖生产库 |
| O6 | `DAILY_SCHEDULER_ENABLED: false` |
| O-stop | 无用户路径则停止，不把备份写入 runtime |

## 回归包（N0 必跑）

```powershell
& $Py scripts\check_architecture.py --strict
& $Py -m ruff check ab_screener\api\routers\legacy_scan.py ab_screener\api\routers\legacy_lab.py --select F821
& $Py -m pytest tests\test_closers_e2_split_regressions.py tests\test_scan_progress_io.py tests\test_lab_task_recovery.py tests\test_openapi_contract_v2.py tests\test_architecture_boundaries.py tests\test_entry_definition_v1_golden.py -q --tb=short
```

## 总评口径

```text
N0 全绿     → ACCEPTED_ENGINEERING_SLICE（E2-FIX）
N0 缺测试或 F821 仍在 → REJECTED
D/O 未做    → 产品总状态仍 BLOCKED（正确）
七闸门全过  → 本轮不应出现；出现则检查 Agent 写错
```
