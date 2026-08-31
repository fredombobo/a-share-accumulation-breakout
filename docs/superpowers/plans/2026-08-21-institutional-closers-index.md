# 个人机构化收口 — 文档索引（2026-08-21）

> 给实现 Agent / 检查 Agent 用。不是宣传材料。  
> **禁止**自行宣布 `PERSONAL_INSTITUTIONAL_READY`。  
> `LIVE_TRADING_ENABLED` 必须保持 false。  
> **当前下一刀（2026-08-22 独立检查后）：** [2026-08-22-closers-next-index.md](2026-08-22-closers-next-index.md) —— 先修 E2 回归再 D/O。本五件套保留为背景合同。

## 读这套文档的顺序

1. 本索引  
2. [现状审计与洞见](2026-08-21-institutional-closers-audit.md) — 冻结事实，先读再动手  
3. [实施计划](2026-08-21-institutional-closers-plan.md) — 分波次做什么、不做什么  
4. [验收矩阵](2026-08-21-institutional-closers-acceptance.md) — 闸门与命令，过了才能进下一波  
5. [实现 Agent 手册](2026-08-21-institutional-closers-agent-runbook.md) — 环境、分支、handoff 模板  

## 本机路径（复制给其它 Agent）

```text
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-21-institutional-closers-index.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-21-institutional-closers-audit.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-21-institutional-closers-plan.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-21-institutional-closers-acceptance.md
E:\CODEX\Stock_selection\accumulation_breakout\docs\superpowers\plans\2026-08-21-institutional-closers-agent-runbook.md
```

## 两层目标（不要混）

| 层 | 目标状态 | 谁可以做 |
|---|---|---|
| A. 项目自己的合同 | 先到 `ENGINEERING_READY_RESEARCH_BLOCKED`；七闸门全过才是 `PERSONAL_INSTITUTIONAL_READY` | 工程 Agent 只能推 A 的 D/O/工程债；R 过不过由真实实验决定 |
| B. Wind / Bloomberg | **不是目标**。本仓库是本机单用户个人研究桌面 | 禁止按商业终端功能表扩 scope |

## 已有、不要重写的计划

| 文档 | 关系 |
|---|---|
| [完善计划 G0–G8](../../IMPROVEMENT-PROGRAM-2026-08-20.md) | 工程债主线。G0 冻结、G1 `--strict` 已过。本套计划接 G2 起 |
| [G2 拆路由](2026-08-21-g2-backend-split.md) | Wave E 直接执行这份，不要另起炉灶 |
| [P8 证据 2026-08-18](../../ACCEPTANCE-V2-P8-EVIDENCE-2026-08-18.md) | 部分过时（PIT 核心表已回填）。以本套 audit 为准 |
| [SUPERCHARGE PIT](../../SUPERCHARGE-PLAN-2026-08-18.md) | 数据完整化。fina/holder/stock_basic 仍空 |
| [备份 Runbook](../../BACKUP-RESTORE-RUNBOOK-V2.md) | Wave O 操作手册 |
| [v2 设计规格](../specs/2026-08-16-institutional-console-v2-design.md) | 七闸门合同。不要改冻结语义 |
| [astock 桥验收](../../ACCEPTANCE-ASTOCK-BRIDGE-V1-2026-08-21.md) | 只读情报已 ACCEPTED。禁止接进 A 池 |

## 用户保护文档（Agent 不得覆盖）

- `docs/STATUS.md`
- `docs/RESEARCH-ROADMAP.md`

## 总状态（审计日）

`BLOCKED`。不是 `PERSONAL_INSTITUTIONAL_READY`，也还不是 `ENGINEERING_READY_RESEARCH_BLOCKED`。
