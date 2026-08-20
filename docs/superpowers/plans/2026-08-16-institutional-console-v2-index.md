# 个人机构化平台 v2.0 文档包总索引

> 这是交给实现 Agent 的唯一入口。v2.0 当前状态为**设计完成、尚未实现**。

## 文档包

1. [设计规格](../specs/2026-08-16-institutional-console-v2-design.md)

   定义产品边界、七闸门、模块架构、PIT、研究、多形态选股、风险、纸面账本、DAG 和控制台。

2. [实施计划](2026-08-16-institutional-console-v2-implementation.md)

   P0–P8 的依赖、精确影响路径、测试先行步骤、阶段出口和回滚方式。

3. [验收矩阵](2026-08-16-institutional-console-v2-acceptance.md)

   D/R/S/P/L/O/G 七闸门的量化阈值、证据、有效期、失败注入、性能预算和最终审计清单。

4. [多 Agent 执行手册](2026-08-16-institutional-console-v2-agent-runbook.md)

   Work Packages、并行波次、共享文件所有权、迁移意图、交接模板和最终审计输入。

5. [v1.1 历史计划](2026-08-11-institutional-console-upgrade.md)

   仅作为历史需求来源；与 v2 冲突时本索引列出的其余七份 v2 文档优先。

6. [数据源与 PIT 合同](../specs/2026-08-16-institutional-console-v2-data-contract.md)

   逐数据集定义来源能力、业务键、时点、权限、覆盖率、原始归档、市场宽度和缺失行为。

7. [六形态策略目录](../specs/2026-08-16-institutional-console-v2-strategy-catalog.md)

   六个 EXPERIMENTAL 形态的公式、参数范围、A/B eligibility、生命周期、outcome 和 golden 测试。

8. [平台契约与运维合同](../specs/2026-08-16-institutional-console-v2-platform-contracts.md)

   ScanProfile、风险公式、API、告警审计、DAG、迁移、备份、证据哈希和可执行命令。

## 实现 Agent 开始前

```text
AGENTS.md
→ tasks/backlog.yaml
→ tasks/implementation_state.yaml
→ 本索引
→ 设计规格
→ 数据/PIT合同、策略目录、平台合同
→ 实施计划
→ 验收矩阵
→ Agent 手册
```

若文档包和 v2 task DAG 尚未进入 Git，集成 Agent 先执行实施计划定义的唯一例外 `V2-P0-BOOTSTRAP`，只创建 planning commit，不改业务代码，也不纳入用户现有脏文件。随后只领取 `status=ready` 且依赖已完成的任务；第一个业务实施包必须是 P0 基线重测和契约冻结，不得直接从页面开发开始。

## 建议阶段状态

| 阶段 | 初始状态 | 完成证据 |
|---|---|---|
| P0 基线/语义/契约 | pending | 新基线 manifest、V1 golden、全量质量门禁 |
| P1 PIT/情报 | pending | PIT gate、as-of universe、源端比对和情报契约 |
| P2 执行核心 | pending | 研究/纸面 parity、零分误差和零/部分成交 fixtures |
| P3 研究治理 | pending | registration、trial ledger、PBO/DSR/MinTRL/Nested WF |
| P4 多形态/信号 | pending | 六插件 registry、漏斗、生命周期和 outcome |
| P5 组合/风险 | pending | 约束拒绝码、风险手算、压力和对账 |
| P6 运维/恢复 | pending | DAG 故障注入、审计链、备份恢复和五日观察 |
| P7 API/UI | pending | OpenAPI、typed clients、浏览器/390px/键盘/恢复 E2E |
| P8 总验收 | pending | 七闸门当前身份全部 PASS 或诚实阻断结论 |

## 最重要的执行纪律

- v2.0 是“个人机构化”，不是商业机构认证或实盘系统。
- 页面完成不等于数据、研究或运维闸门通过。
- 只有 D/S/P/L/O/G 已全部 PASS 而 R 单独未通过时，正确结果才是 `ENGINEERING_READY_RESEARCH_BLOCKED`；其他硬门失败仍为 `BLOCKED`。
- `LIVE_TRADING_ENABLED` 永远为 false，不得新增券商真实下单能力。
- 当前 `docs/RESEARCH-ROADMAP.md` 与 `docs/STATUS.md` 有用户修改，集成前必须人工合并，禁止覆盖。
- 任何 Agent 完成后都必须交 handoff；最终结论由独立审计者给出。

## 最终让审计 Agent 检查时

请提供本索引、clean RC commit、全部 handoff、七闸门 JSON、总 evidence index、迁移 manifest、全量质量门禁、浏览器/性能/故障注入/备份恢复报告及真实研究结论。
