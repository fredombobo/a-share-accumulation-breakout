# CLOSERS-NEXT 交接总索引（2026-08-22 23:53）

> 给下一个接手 Agent 的总入口。先读本文件，再按需读各 handoff / 四件套。
> 结论纪律：只能写 READY_FOR_REVIEW / BLOCKED；禁止宣布 PERSONAL_INSTITUTIONAL_READY。

## 0. 项目与运行环境

```text
项目根:  E:\CODEX\Stock_selection\accumulation_breakout
Python:  .\.venv312\Scripts\python.exe  （3.12.10，唯一证据环境）
服务:    127.0.0.1:8001（单端口托管后端 + 前端 dist，当前在跑）
数据库:  runtime\stock_data.db（12.9 GB，75 表）
```

## 1. git 状态

```text
分支:    closers-g2-split（14 commit，领先 main 2c04962）
PR:      #2 open（未合并）https://github.com/fredombobo/a-share-accumulation-breakout/pull/2
main:    2c04962（未变）
⚠️ git 2.55 ref 持久化 bug：fetch 后 origin/main 常访问不到，用 `git ls-remote origin main` 核实；
   分支命名禁用 codex/ 前缀（写 ref 失败）。
```

## 2. 已完成（实现侧全部落地）

| 波次 | 内容 | handoff |
|---|---|---|
| E2 | backend_app 3014→276 行，拆 8 模块 | `docs/handoffs/CLOSERS-E2-2026-08-22.md` |
| E3 | 6 根脚本迁 ab_screener + re-export | `docs/handoffs/CLOSERS-E3-2026-08-22.md` |
| N0 | E2-FIX（2 NameError）+ 3 回归测试 | `docs/handoffs/CLOSERS-N0-E2-FIX-2026-08-22.md` |
| N2 | D1 日线→20260821 + D2 PIT 四表回填 + 生产库同步 | `docs/handoffs/CLOSERS-N2-D-2026-08-22.md` |
| N3 | O-min 2 份校验备份 | `docs/handoffs/CLOSERS-N3-O-2026-08-22.md` |

全量测试基线：**662 passed, 1 failed**（唯一失败 = baseline 采集超时，非缺陷）。

## 3. 未完成（下一步可推进的）

| 项 | 状态 | 阻塞原因 | 需要的输入 |
|---|---|---|---|
| **PR #2 合并** | open | 等 review | 检查 Agent / 用户 Merge |
| **R 研究** | ❌ INSUFFICIENT_EVIDENCE | **OOS 回撤 0.56 > 25%**（策略真实表现）+ WF/反过拟合证据不完整 | 更大的网格重跑（max_codes=600）或接受策略 FAIL |
| **O 完整** | ⏳ | 7 份备份 + 5 日 soak 需跨 7 交易日 | 最早 9/1，无法今晚 |
| **F 旗标** | 🔒 | 计划硬约束：实现 Agent 禁止开生产旗标 | **用户决策** |
| **G 总验收** | 🔒 | 必须独立复跑 | **检查 Agent** |

## 4. 关键产物位置

```text
四件套:   docs\superpowers\plans\2026-08-22-closers-next-{index,plan,acceptance,agent-runbook}.md
独立检查: docs\ACCEPTANCE-CLOSERS-NEXT-2026-08-22.md（检查 Agent 产物）
备份:     E:\ab-backups\backup_20260822_160217.db 与 backup_20260822_223238.db
PIT 副本: E:\ab-maintenance\stock_data_copy.db（已与生产库一致）
coverage: runtime\v2\pit_coverage_20260822_221256.json
研究报告: runtime\v2\research_A_20260822_233427.json\trusted_report_20260822_233442.json
```

## 5. 关键坑（接手必读，避免重踩）

1. **git 2.55**：`codex/` 前缀分支写 ref 失败 → 用单层分支名；origin/main ref 不持久 → 用 ls-remote 核实。
2. **re-export**：`from x import *` 不导私有名（用 globals 转发）；迁移后 `__file__` 基准变（改 parents[1]）；monkeypatch 需 patch 真模块。
3. **PIT history 表 append-only**：cyq/fina/holder_history 有 no_delete 触发器，只能增量 INSERT，禁止 DELETE。
4. **GitHub token**：fine-grained PAT 需 Contents=Read and write；classic token 改 workflow 文件需 `workflow` scope。
5. **restore_backup.ps1**：有 UTF-8 BOM（efbbbf），headless PowerShell 下 exit 1 无输出；DryRun 校验可用 Python 等价替代。
6. **研究重跑**：`run_trusted_research_real.py` 默认 max_codes=400 导致 WF/反过拟合证据不完整；OOS 回撤是策略真实表现。

## 6. 下一步建议（给接手 Agent）

1. **R 补完整证据**：`run_trusted_research_real.py --max-codes 600 --step 5 --strategy A`（完整网格）重跑，让判定从 INSUFFICIENT_EVIDENCE 变明确的 PASS/FAIL。
2. **PR #2**：review 后 Merge（需要用户/检查 Agent 操作，或给工程 Agent 推送权限）。
3. **O**：从 8/24（周一）开始每日备份 + soak 采集，凑 7 份 + 5 日。
4. **F 决策清单**：整理每个生产旗标（含义/风险/建议时机）给用户拍板。
5. **G**：由检查 Agent 按 acceptance 矩阵独立复跑。
