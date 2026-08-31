# 文档索引（INDEX）

> accumulation_breakout「横盘吸筹 → 启动」选股系统 · v2 机构级控制台
> 更新：2026-08-29 · 本索引指向当前有效文档；历史验收记录见文末 archive。

## 入门与使用

- [小白使用手册](./小白使用手册.md) —— 双击启动、扫描、看结果的完整步骤
- [操作手册](./操作手册.md) —— 日常操作与各页面说明
- [USER-GUIDE](./USER-GUIDE.md) —— 用户指南
- [DEPLOYMENT](./DEPLOYMENT.md) —— 部署说明

## 核心契约（v2，当前有效）

- [API-CONTRACT-V2](./API-CONTRACT-V2.md) —— v2.0 API 契约（幂等 / 错误 envelope / feature flags）
- [ENTRY-DEFINITION-V1](./ENTRY-DEFINITION-V1.md) —— A 池入场定义（冻结 v1：突破日下一交易日开盘）
- [ENTRY-DEFINITION-V2](./ENTRY-DEFINITION-V2.md) —— 入场定义 v2（激活不改变 V1）
- [EXECUTION-MODEL-V2](./EXECUTION-MODEL-V2.md) —— 唯一执行领域核心（整数分/微元账务、撮合、FIFO/T+1）
- [ERROR-CODES-V2](./ERROR-CODES-V2.md) —— 错误码注册表
- [DATA-DICTIONARY-PIT-V2](./DATA-DICTIONARY-PIT-V2.md) —— PIT 时点数据字典（五元组 / 历史表）
- [DSL-REFERENCE](./DSL-REFERENCE.md) —— 逻辑平台 DSL 参考

## 状态与路线图（当前有效）

- [STATUS](./STATUS.md) —— 项目状态看板（用户维护，勿由 Agent 覆盖）
- [RESEARCH-ROADMAP](./RESEARCH-ROADMAP.md) —— 研究路线图（用户维护）
- [收口下一刀 2026-08-22](./superpowers/plans/2026-08-22-closers-next-index.md) —— **当前实现入口**（先修 E2 NameError，再 D/O）
- [个人机构化收口五件套](./superpowers/plans/2026-08-21-institutional-closers-index.md) —— 收口主线背景（D/O/E/R/F/G）
- [IMPROVEMENT-PROGRAM-2026-08-20](./IMPROVEMENT-PROGRAM-2026-08-20.md) —— 完善计划 G0–G8（工程债主线）
- [SUPERCHARGE-PLAN-2026-08-18](./SUPERCHARGE-PLAN-2026-08-18.md) —— 深度增强计划（PIT 完整化 / 风险模型 / 因子库）
- [VOLUME-PRICE-LOGIC-PLATFORM](./VOLUME-PRICE-LOGIC-PLATFORM.md) —— 量价预测·逻辑生成平台规格

## 运维

- [BACKUP-RESTORE-RUNBOOK-V2](./BACKUP-RESTORE-RUNBOOK-V2.md) —— 备份/回滚 Runbook
- [龙虎榜研究产品运行手册](./LHB-PRODUCT-RUNBOOK.md) —— 隔离副本、每日盘后流水线、启动与安全边界
- [ADR](./ADR/) —— 架构决策记录

## 交接与近期验收

- [ACCEPTANCE-LHB-V1](./ACCEPTANCE-LHB-V1.md) —— 龙虎榜研究产品验收；本地产品 READY、研究状态 RESEARCH_BLOCKED
- [龙虎榜实施清单](./LHB-TRACKING-IMPLEMENTATION-CHECKLIST.md) —— T01–T12 功能、验收标准与剩余硬门
- [HANDOFF-V2-P0-P3-2026-08-18](./HANDOFF-V2-P0-P3-2026-08-18.md) —— v2 P0–P3 交接
- [ACCEPTANCE-V2-P8-EVIDENCE-2026-08-18](./ACCEPTANCE-V2-P8-EVIDENCE-2026-08-18.md) —— P8 最终验收证据索引
- [ACCEPTANCE-ASTOCK-BRIDGE-V1-2026-08-21](./ACCEPTANCE-ASTOCK-BRIDGE-V1-2026-08-21.md) —— astock 情报桥 v1 验收
- [ACCEPTANCE-CLOSERS-2026-08-22](./ACCEPTANCE-CLOSERS-2026-08-22.md) —— 收口独立检查（E2 须修 / E3 切片通过；已被下一刀覆盖）
- [ACCEPTANCE-CLOSERS-NEXT-2026-08-22](./ACCEPTANCE-CLOSERS-NEXT-2026-08-22.md) —— 下一刀独立检查（N0 通过 / N1–N3 未做 / 总状态 BLOCKED）
- [handoffs/](./handoffs/) —— 各任务 handoff 文档
- [superpowers/plans/](./superpowers/plans/) —— 冻结实施计划与验收矩阵

## 历史验收（archive）

> 以下为历史阶段验收记录，保留存档，不再作为当前契约依据。

- ACCEPTANCE-2026-08-06.md / ACCEPTANCE-REPORT-2026-08-06.md / ACCEPTANCE-REPORT-INDEPENDENT-2026-08-06.md
- ACCEPTANCE-V2-P0.md
- BEGINNER-UI-ACCEPTANCE-2026-08-08.md
- FINAL-ACCEPTANCE.md / FINAL-ACCEPTANCE-2026-08-08.md
- HANDOFF-2026-08-06.md
- LAB-TRUSTED-REPORT-ACCEPTANCE-2026-08-08.md
- LOGIC-PLATFORM-PHASE1/2/3-ACCEPTANCE-2026-08-08.md
- NINE-POINT-CLOSED-LOOP-ACCEPTANCE-2026-08-11.md
- PHASE0/1/2/3-7/8-ACCEPTANCE-2026-08-07~08.md
- ROLLBACK-2026-08-07.md
- UPGRADE-SYSTEM-ACCEPTANCE-2026-08-08.md / UPGRADE-SYSTEM-FIX-2026-08-08.md
- 改进计划-2026-08-03.md
