# 个人机构化收口 — 实现 Agent 手册（2026-08-21）

先读：[索引](2026-08-21-institutional-closers-index.md) → [审计](2026-08-21-institutional-closers-audit.md) → [计划](2026-08-21-institutional-closers-plan.md) → [验收](2026-08-21-institutional-closers-acceptance.md) → 根 `AGENTS.md`。

只领 **一个** Wave（D / O / E2 / E3-E6 / R）。不要同时改生产库和 `backend_app.py`。

## 工作树

```text
仓库: E:\CODEX\Stock_selection\accumulation_breakout
远程: https://github.com/fredombobo/a-share-accumulation-breakout
```

- 从 `origin/main` 开独立分支，例如：
  - `codex/closers-d-pit`
  - `codex/closers-o-backup`
  - `codex/closers-g2-split`
- **不要覆盖** `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md`
- **不要改** `configs/platform_v2.yaml` 生产旗标（除非用户点名 Wave F）
- 不要把 `astock` 仓拷进本仓

## Git（本机 PATH 经常没有 git）

```powershell
$git = "C:\Users\13818\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe"
# 若 cmd\git.exe 不存在，再试 mingw64\bin\git.exe —— 不要用已删除的 bin\git.exe
Set-Location E:\CODEX\Stock_selection\accumulation_breakout
& $git status -sb
& $git fetch origin --prune
& $git log --oneline origin/main -5
```

提交信息前缀建议：`feat(closers-d):` / `feat(closers-g2):` / `docs(closers):`

## Python 证据环境（强制）

已知坑：

- `C:\Python314\python.exe` **无 pytest**，不得当证据。
- `.venv312\Scripts\python.exe` 可能因 `pyvenv.cfg` 的 `home` 失效而无法启动。
- 审计日可用的解释器：

```powershell
$Py = "E:\C_Drive_Moved_2026-06-03\AppData_Junctions\AppData\Local\Programs\Python\Python312\python.exe"
$Root = "E:\CODEX\Stock_selection\accumulation_breakout"
$env:PYTHONPATH = "$Root;$Root\.venv312\Lib\site-packages"
Set-Location $Root
& $Py -c "import sys; print(sys.version)"
# 必须看到 3.12.x
```

若 `.venv312\Scripts\python.exe` 能启动且 `python -c "import sys; print(sys.version)"` 为 3.12.10，优先用它，不必设 PYTHONPATH。

清代理：

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
$env:http_proxy=$env:https_proxy=$env:all_proxy=$null
# 然后再设本仓库 PYTHONPATH（若用 junction 解释器）
```

Tushare：`from tushare_init import pro`。Token 只来自环境 / `.env`，不写进文档和测试 fixture。

## 前端

`npx` 可能被 PowerShell execution policy 拦截：

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout\web\frontend
node .\node_modules\typescript\bin\tsc --noEmit
```

## 每波必跑回归

```powershell
& $Py scripts\check_architecture.py --strict
& $Py -m pytest tests\ -q -k "not browser" --tb=short
```

G2 / 前端改动再加 tsc。失败 = 该波未完成，先修再报。

## 旗标自检（防误开）

```powershell
& $Py -c "from ab_screener.application.platform_config import load_resolved_config
c=load_resolved_config()
print('\n'.join(f'{k}={v}' for k,v in c['flags'].items()))"
```

期望：`LIVE_TRADING_ENABLED=False`；除 `V2_EXECUTION_DUAL_RUN_ENABLED` 外生产项 False。

## PIT 回填（Wave D）红线

```powershell
# 错误：打生产库
# & $Py scripts\backfill_pit_v2.py --db E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db --run

# 正确：绝对路径副本
& $Py scripts\backfill_pit_v2.py --db E:\ab-maintenance\stock_data_copy.db --preflight
```

`--db` 必须绝对路径。空间不足 preflight FAIL 则停。`fina_indicator` 必须按 ts_code 分区。

## 备份（Wave O）红线

- 没有用户给的 `AB_BACKUP_ROOT` → 停止，handoff 写等待。
- 不要把 7 份 12GB 拷进 `runtime\`。
- 恢复演练目标用独立文件，不要直接写 `runtime\stock_data.db`。

## 完成时必须提交

1. 该波源代码 / 脚本 / 测试（若有）
2. 证据文件路径（coverage json、备份日志、pytest 尾部）
3. `docs/handoffs/CLOSERS-<WAVE>-<DATE>.md`（下方模板）
4. 不得自称 ACCEPTED / READY / PERSONAL_INSTITUTIONAL_READY

## Handoff 模板

```markdown
# CLOSERS-<WAVE> Handoff

## 身份
- Agent:
- 基线 commit:
- 交付 commit:
- 契约版本: PERSONAL-INSTITUTIONAL-CLOSERS-2026-08-21
- 波次: D | O | E2 | E3 | E4 | E5 | E6 | R | F | G

## 环境
- Python 可执行文件:
- sys.version:
- git:

## 完成范围
- [ ]

## 明确未完成
- [ ]

## 修改文件
- added / modified:
- 是否改 platform_v2.yaml: no | yes（必须解释）
- 是否改 STATUS.md: no（必须 no）
- shared hotspot: backend_app / paper_trading / signals / 无

## 测试证据
- 精确命令、退出码、passed 数:

## 产物证据
- 是否使用真实 Token:
- 是否修改 runtime 账本:
- 是否写生产 DB PIT:
- daily MAX:
- 旗标打印:

## 闸门自测（实现侧，非正式验收）
- 本波验收 ID 与结果:

## 结论
- READY_FOR_REVIEW | BLOCKED
- 建议总状态（不得写 PERSONAL_INSTITUTIONAL_READY）: BLOCKED | ENGINEERING_READY_RESEARCH_BLOCKED（仅当 D+O 证据已齐且 R 仍 FAIL）
```

## 检查 Agent

收到 handoff 后按验收矩阵 **重跑命令**，写：

```text
docs/ACCEPTANCE-CLOSERS-YYYY-MM-DD.md
```

总评只能是 `ACCEPTED_ENGINEERING_SLICE` / `REJECTED` / `BLOCKED`。

## 给用户粘贴的开工提示词（按人领取）

### 数据 Agent

```text
你是 accumulation_breakout 的实现 Agent。只做 Wave D。
根：E:\CODEX\Stock_selection\accumulation_breakout
必读：
docs/superpowers/plans/2026-08-21-institutional-closers-index.md
以及同目录 audit / plan / acceptance / agent-runbook。
硬约束：LIVE false；PIT 只打绝对路径副本；不开 V2_PIT_READ_ENABLED；
不改 STATUS.md；不改入场 V1；astock 不进 A 池。
完成后写 docs/handoffs/CLOSERS-D-YYYY-MM-DD.md，结论只能 READY_FOR_REVIEW 或 BLOCKED。
```

### 运维 Agent

```text
只做 Wave O-min（AB_BACKUP_ROOT + 1 份校验备份 + 恢复演练）。
未提供 AB_BACKUP_ROOT 则停止并在 handoff 说明。
不要把备份写进 runtime\。不要开启 DAILY_SCHEDULER_ENABLED。
必读同上 closers 五件套。
```

### G2 Agent

```text
只做 Wave E2。权威方案：
docs/superpowers/plans/2026-08-21-g2-backend-split.md
五步，每步 pytest -k "not browser" 全绿才下一步。
不改旗标、不改入场、不改研究结论。
```

### 研究 Agent

```text
只做 Wave R：复跑可信研究，保持 fail-closed。
FAIL 是可接受交付。禁止改阈值让报告变绿，禁止晋级候选进 A 池。
ENTRY V1 golden 必须仍绿。
```
