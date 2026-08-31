# 收口下一刀 — 文档索引（2026-08-22）

> 给实现 Agent 用。基于独立检查 `docs/ACCEPTANCE-CLOSERS-2026-08-22.md`。  
> **禁止**宣布 `PERSONAL_INSTITUTIONAL_READY`。`LIVE_TRADING_ENABLED` 保持 false。

## 读序

1. 本索引  
2. [落地计划](2026-08-22-closers-next-plan.md) — 步骤、文件、禁止项  
3. [验收矩阵](2026-08-22-closers-next-acceptance.md) — 闸门 ID 与命令  
4. [Agent 手册](2026-08-22-closers-next-agent-runbook.md) — 环境、handoff、可粘贴提示词  

背景（不要当本轮 backlog 重做）：  
[2026-08-21 收口五件套](2026-08-21-institutional-closers-index.md) · [独立检查](../../ACCEPTANCE-CLOSERS-2026-08-22.md)

## 本机路径（复制给其它 Agent）

```text
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-22-closers-next-index.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-22-closers-next-plan.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-22-closers-next-acceptance.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-22-closers-next-agent-runbook.md
```

## 本轮只做这四刀（按序）

| 刀 | 谁领 | 能否并行 | 目标 |
|---|---|---|---|
| **N0 E2-FIX** | 工程 Agent | 先做；阻断合入 | 修扫描完成 / Lab JSON 的 `NameError`，补测试，ruff F821=0 |
| **N1 PR** | 同一工程 Agent 或用户 | N0 全绿后 | 把 `closers-g2-split`（含 E3 + N0）合进 `origin/main` |
| **N2 Wave D** | 数据 Agent | 可与 N0 并行（不改同一文件） | `sync_daily` + PIT 空表副本回填；**不开** PIT 读旗标 |
| **N3 Wave O-min** | 运维 Agent | 要用户先给 `AB_BACKUP_ROOT` | 1 份校验备份 + 恢复演练；不开调度旗标 |

不做：开 LIVE、改 V1 入场、研究 FAIL 变绿、astock 进 A 池、Wave F 开生产旗标、宣布机构级就绪。
