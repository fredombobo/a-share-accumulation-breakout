# 收口下一刀 — 落地计划（2026-08-22）

> 契约版本：`CLOSERS-NEXT-2026-08-22`  
> 基线：独立检查 `docs/ACCEPTANCE-CLOSERS-2026-08-22.md`  
> 代码：本地分支 `closers-g2-split` @ `a86b03e`（检查日）；实现前先 `git log -1` 复核  
> 目标：先让 E2 **可合入**，再收 D/O。不追求本轮 `PERSONAL_INSTITUTIONAL_READY`。

## 0. 硬约束

1. `LIVE_TRADING_ENABLED=false`。禁止券商适配器。  
2. 不改 `A_POOL_STRICT_NEXT_OPEN_V1`。  
3. 不覆盖 `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md`。  
4. 不改 `configs/platform_v2.yaml` 把生产旗标改为 true（含 `V2_PIT_READ_ENABLED`）。  
5. PIT `--run` 只打 **绝对路径副本**，禁止直接打 `runtime/stock_data.db`。  
6. astock / 情报不进 A 池、扫描、纸面。  
7. 禁止 `INSERT OR REPLACE` 进生产路径。  
8. 证据 Python = 3.12（`.venv312\Scripts\python.exe`）。禁止 3.14 当唯一证据。  
9. 分支名不要用 `codex/` 前缀（本机 git 写 ref 会失败）。用 `closers-e2-fix` 这种单层名。  
10. 结论只能 `READY_FOR_REVIEW` / `BLOCKED`。不得写 READY / ACCEPTED / `PERSONAL_INSTITUTIONAL_READY`。

## 现状（检查日冻结，动手前复核）

- E3 根脚本迁包：**切片通过**  
- E2 拆路由：结构完成，**扫描成功路径会 `NameError`**  
- 日线 MAX=`20260818`；`stock_basic_history` / `fina_indicator_history` / `holder_history` = 0  
- `AB_BACKUP_ROOT` 未设；无 soak；`dag_runs=0`  
- GitHub `origin/main` 仍是 `2c04962`；产品未更新  

## N0 — E2-FIX（必须先做，阻断合入）

### 缺陷 1：扫描完成路径

文件：`ab_screener/api/routers/legacy_scan.py`

现在只从 `legacy_state` 导入：

```python
from ab_screener.api.legacy_state import (
    _PARENT,
    _SCAN_CANCEL_EVENTS,
    _SCAN_LOCK,
    _SCAN_TASKS,
    _SCAN_TASKS_MAX,
    _store,
)
```

但完成扫描时（约 L315–347）使用了 **未导入** 的：

- `_BUILD_VERSION`（`complete_scan_run(..., code_version=_BUILD_VERSION)`）
- `_OVERVIEW_CACHE`（清总览缓存，避免展示旧扫描）

`legacy_state.py` 里这两个名字已经存在，是搬家时漏导入，不是缺实现。

**改法：** 把 `_BUILD_VERSION`、`_OVERVIEW_CACHE` 加入上述 import。不要在 `legacy_scan` 里重新计算 build version，不要新建第二份 cache dict。

同文件 `_run_scan_worker` 内已有 `import json`（约 L102），json 不是本缺陷。

### 缺陷 2：Lab JSON 下载

文件：`ab_screener/api/routers/legacy_lab.py`  
函数：`lab_report_download` 约 L632–638

```python
body = json.dumps(_report_payload(record), ...)
```

模块顶层 **没有** `import json`。`format=json` 会 `NameError`。

**改法：** 顶层加 `import json`（文件已有 `hashlib`/`uuid` 等，放在标准库块）。

### 测试（本刀必须新增，禁止只靠「全量 659 绿」）

新建 `tests/test_closers_e2_split_regressions.py`（名称可微调，但必须是独立文件，方便检查 Agent 点名复跑）。

最少 3 个用例：

1. **扫描模块绑定**  
   `from ab_screener.api.routers import legacy_scan as m`  
   断言 `hasattr(m, "_BUILD_VERSION")` 且值为非空 str；  
   断言 `m._OVERVIEW_CACHE` 是 dict 且含 `key`/`payload`。  
   （证明 import 进了模块命名空间，不是只存在于 `legacy_state`。）

2. **清缓存可执行**  
   给 `m._OVERVIEW_CACHE` 写入脏 `key`/`payload`，调用一个 **小函数**（若完成路径是内联的，允许在 `legacy_scan.py` 抽出 4 行）：

   ```python
   def _clear_overview_cache() -> None:
       _OVERVIEW_CACHE["key"] = None
       _OVERVIEW_CACHE["payload"] = None
   ```

   完成路径改为调用它。测试断言调用后 key/payload 为 None。  
   **不要**为测这个去跑全市场扫描子进程。

3. **Lab JSON 下载**  
   仿 `tests/test_lab_task_recovery.py`：`monkeypatch.setattr(legacy_lab, "_LAB_STORE", store)`，  
   造一条带 `report_markdown` 的 run，直接调 `lab_report_download(run_id, format="json")`。  
   断言不抛 `NameError`，`status_code==200` 或返回 `Response` 且 `media_type` 含 `json`，body 能 `json.loads`。

禁止：用「ruff 过了」代替这 3 个测试。

### 清理（同一 commit 可做）

- `web/backend_app.py` 顶层未使用的 `pandas` / `BaseModel` / `signals` / `scoring` 和整段未用的 `legacy_state` 导入：删掉。宿主只保留装配真正用到的符号（`_store`、`_DB`、`_LOGGER`、`_paper_enabled` 等）。  
- 本刀改过的文件跑 ruff，F821 必须 0。F401/I001 在 **本刀改动文件** 上尽量 0。  
- 不要顺手重写扫描算法、不要改进度文件协议。

### N0 完成定义

- 3 个新测试绿  
- `ruff check ab_screener/api/routers/legacy_scan.py ab_screener/api/routers/legacy_lab.py --select F821` exit 0  
- `check_architecture.py --strict` exit 0  
- 定向 pytest：新文件 + `test_scan_progress_io.py` + `test_lab_task_recovery.py` + `test_openapi_contract_v2.py` + `test_architecture_boundaries.py` + `test_entry_definition_v1_golden.py` 绿  
- 全量 `pytest tests/ -q -k "not browser"`：允许 **仅** `test_identity_stable_across_runs` 因 12GB 库 120s 超时失败；其它失败 = N0 未完成  
- 旗标未改；STATUS 未改  
- handoff：`docs/handoffs/CLOSERS-N0-E2-FIX-YYYY-MM-DD.md`

## N1 — 合入 main

N0 检查通过后再做。

1. 确认分支包含：E2 拆分 + E3 迁包 + N0 修复 + 测试。  
2. 从最新 `origin/main` rebase 或 merge（检查日 main=`2c04962`）。  
3. 开 PR。注意 `.github/workflows/ci.yml` 的 push 分支过滤是 `main` 与 `codex/**`，**当前分支名 `closers-g2-split` 不会在 push 时跑 CI**；必须走 **pull_request** 才会跑。  
4. CI 步骤含 `ruff check .`。若全仓 ruff 红在 **N0 未改的旧文件**，handoff 列出路径，**不要**借机大扫全仓格式。N0 自己的文件必须绿。  
5. 合入后本地 `git fetch` 确认 `origin/main` 含修复 commit。  
6. 不要在 PR 描述里写机构级就绪。

若用户要求「先不推远程」：N1 标 BLOCKED（等待用户），N2/N3 仍可在本地做。

## N2 — Wave D 数据收口（可与 N0 并行，禁止碰 N0 文件）

**不开** `V2_PIT_READ_ENABLED`。

### D1 日线新鲜度

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
& $Py sync_daily.py
```

验收：`SELECT MAX(trade_date) FROM daily` = 源端最近 **已收盘** 交易日（16:00 前允许仍是上一交易日）。  
检查日是 `20260818`，已滞后。  
不要自动把旧 `scan_result`（检查日 MAX=`20260814`）当成今日 A 池。重扫由用户或后续任务决定，本刀不强制全市场扫描。

### D2 PIT 只打副本

```text
副本建议：E:\ab-maintenance\stock_data_copy.db
禁止：--db ...\runtime\stock_data.db
```

```powershell
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --preflight
# 空间不够 = 停
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --run --datasets stock_basic
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --run --start 20220819 --end <源端最新> --datasets cyq --workers 4
# holder / fina_indicator 按 ts_code 分区；禁止无分区全市场 fina 循环
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --coverage
```

优先级：

| 先 | 数据集 | 检查日状态 |
|---|---|---|
| 不要重跑 | daily / daily_basic / adj_factor / moneyflow | 已 976/976 |
| P1 | `stock_basic` | 0 行 |
| P1 | `cyq` | 548/976 已 done，续跑剩余 |
| P2 | `holder`、`fina_indicator` | 0；权限/耗时不够则书面 INSUFFICIENT，禁止假行 |
| skip | 公告/新闻/一致预期/同花顺概念 | 权限不足则 INSUFFICIENT |

coverage 落盘：`runtime/v2/pit_coverage_<stamp>.json`（可写 runtime；json 不是 12GB 库）。

副本验证通过后 **经用户确认** 再同步生产库。Agent 不得悄悄覆盖 `runtime/stock_data.db`。

公司行为账本 `corporate_actions=0`：有接口就按 repository **追加**；没权限记 INSUFFICIENT。本刀不因此把 D 标 PASS，也不开 PIT 读。

handoff：`docs/handoffs/CLOSERS-N2-D-YYYY-MM-DD.md`

## N3 — Wave O-min（要用户路径）

未提供 `AB_BACKUP_ROOT` → **停止**，handoff 写「等待用户」，不要把备份写进 `runtime\`。

要求：绝对路径、可写、**不在** `runtime\` 下，最好独立盘。7 份 × ~12GB ≈ 84GB+，先算空间。

按 `docs/BACKUP-RESTORE-RUNBOOK-V2.md`：

1. `create_backup(db, backup_root)` → 1 份校验通过的备份（O-min）  
2. `scripts\restore_backup.ps1 -BackupRoot <根> -RestoreTo <演练目标.db> -DryRun`  
   用户确认后去掉 DryRun，计时 ≤1800s。演练目标 **不得** 覆盖生产库。  
3. 不要开 `DAILY_SCHEDULER_ENABLED`  
4. 不要把 `pt_cycle=10` 当成 soak。满 5 个 COMPLETE 交易日 soak 是后续，本刀不强制。

handoff：`docs/handoffs/CLOSERS-N3-O-YYYY-MM-DD.md`

## 明确不做（写进每个 Agent 提示词）

- Wave F 开任何生产旗标  
- 改研究阈值让 600 股 FAIL 变 PASS  
- 晋级 `research_candidates`  
- 重做 G2 五步拆分（已经拆完，只修漏导入）  
- 把 Logic Platform 接进纸面  
- 商业终端功能（OMS/L2/新闻伪造）

## 建议分工

| Agent | 刀 | 冲突文件 |
|---|---|---|
| Eng-fix | N0 → N1 | `legacy_scan.py` `legacy_lab.py` `backend_app.py` `tests/test_closers_e2_split_regressions.py` |
| Data | N2 | `runtime` 副本、`sync_daily`；**不要**改 N0 文件 |
| Ops | N3 | 备份根；不要改代码，除非 runbook 脚本有 bug |

同一工作树禁止两个 Agent 同时改 `legacy_scan.py`。
