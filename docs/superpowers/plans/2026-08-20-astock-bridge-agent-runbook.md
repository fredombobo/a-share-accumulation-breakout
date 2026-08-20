# Astock 情报桥 v1 — 实现 Agent 手册

先读：[索引](2026-08-20-astock-bridge-index.md) → [计划](2026-08-20-astock-intelligence-bridge.md) → [验收](2026-08-20-astock-bridge-acceptance.md) → 根 `AGENTS.md`。

## 工作树

- 在 `accumulation_breakout` 开独立分支，例如 `codex/astock-bridge-v1`。
- **不要覆盖** 用户未提交的 `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md`。
- 不要把 `E:\CODEX\Stock_selection\astock` 整仓拷进 AB。

## 完成时必须提交

1. 计划所列源码与测试  
2. `docs/handoffs/ASTOCK-BRIDGE-V1.md`（用下方模板）  
3. 不得自称 ACCEPTED  

## Handoff 模板

```markdown
# ASTOCK-BRIDGE-V1 Handoff

## 身份
- Agent:
- 基线 commit:
- 交付 commit:
- 契约版本: ASTOCK-INTELLIGENCE-BRIDGE-V1

## 完成范围
- [ ]

## 明确未完成
- [ ]

## 修改文件
- added / modified:
- shared hotspot touched: no | yes（说明）

## 测试证据
- 精确命令与退出码:

## 产物证据
- 是否使用真实 Token: no
- 是否修改 runtime 账本: no

## 闸门自测（实现侧，非正式验收）
- G1–G8:

## 结论
- READY_FOR_REVIEW | BLOCKED
```

## 检查 Agent（本会话提出计划的一方）

收到 handoff 后按验收矩阵 **重跑命令**，写 `docs/ACCEPTANCE-ASTOCK-BRIDGE-V1-YYYY-MM-DD.md`，总评只能是 ACCEPTED / REJECTED / BLOCKED。
