# Breakout V2 研究与晋级路线图

> 更新：2026-08-28
>
> 项目：`accumulation_breakout`（AB-Screener · 横盘吸筹突破）

## 当前研究裁决

当前为 **FAIL / NO_CANDIDATE**，不能晋级候选参数，更不能进入 A 池或生成订单。

当前权威实验 `v2auth20260828f` 使用代码 `d5707c22c645`、600 股冻结 PIT 快照和共享组合
账户完整运行：OOS 组合净收益 8.49%、PF 1.223、最大回撤 9.18%，且 2× 成本仍为正；但
PBO 0.3125、DSR 0.463596、MinTRL 覆盖 0.188、嵌套参数测试仅 1/5 正收益，未通过
`ROBUST_PERSONAL_V2`。失败是有效研究结果，禁止为了通过而调整阈值、删除失败窗口或复制旧 PASS。

## 强制隔离

| 区域 | 用途 | 能否进入交易链 |
|---|---|---|
| 参数研究 | 预登记、IS/OOS、Nested WF、成本/容量压力、PBO/DSR/MinTRL | 否；PASS 也只成为隔离候选 |
| Shadow 观察 | 用当前定义产生不可变 observation，跟踪 outcome | 只有满足 S 门的正式 profile 才可申请晋级 |
| A 池候选 | 当前交易日扫描后的人工研究候选 | 仍需纸面订单确认和风控 |
| 纸面交易 | 下一可交易日开盘仿真、账本和对账 | 仅模拟；不连接券商 |

## R 闸门：重建当前身份权威研究

必须使用当前代码、配置、数据库、策略语义、成本和撮合身份：

1. 冻结 ENTRY、股票池、日期窗、参数网格、随机种子、成本模型和基准。
2. 在正式运行前写入实验登记和 trial ledger，禁止看结果后补登记。
3. 使用完整 PIT 数据运行净成本 IS/OOS 与 Nested Walk-Forward。
4. 默认输出随机基线与 MA20/60 基线；报告是否 beat 两个基线。
5. 运行 1×/2×/3×成本压力、ADV/参与率容量、参数扰动和消融。
6. 计算 PBO、DSR 和 MinTRL；证据不足必须返回 `INSUFFICIENT`。
7. 将产物哈希和当前身份写入生产事实库，供 `/api/v2/readiness` 复算。

研究结果可以是 PASS、FAIL 或 INSUFFICIENT；只有完整证据下的 PASS 才能成为
`CANDIDATE`，仍不得自动进入 A 池或创建订单。

## S 闸门：从候选到可观察策略

六类策略插件和生命周期代码已经实现，但生产样本为空。下一阶段必须：

1. 正式注册带版本和 semantic hash 的 `strategy_profile`，初始状态为 `EXPERIMENTAL`。
2. 在 Shadow/Paper 模式生成 append-only signal observation/event/projection。
3. 用真实可用时点记录成交可能性和 5/10/20 日 outcome；无法成交保持 `NULL`，不得填零。
4. 按月数、样本量、净成本、双基准、回撤和稳定性阈值复验。
5. 只有 S 门通过后才允许申请 `ACTIVE_FOR_A_POOL`；任何晋级必须可审计、可撤销。

## P/L/O/G 联动条件

研究结论不能脱离生产闭环单独晋级。最终 P8 还要求：

- P：当前身份的生产风险快照和约束结果可复算；
- L：最新完成交易日的扫描→周期→风险→对账→日清清单全部 COMPLETE；
- O：至少 7 份验证备份、严格恢复成功、5 个真实完成交易日 soak；
- G：供应商链路有 TLS、审计 hash chain 有数据库外锚点，且实盘开关始终关闭。

## 可复述的一页结论

最终研究报告首屏必须固定回答：

- 样本与数据时点是否充分；
- 净成本 OOS/WF 是否通过；
- 是否战胜随机与均线基线；
- 成本、容量和参数扰动下是否稳定；
- PBO/DSR/MinTRL 是否过门；
- 裁决是“候选”“不建议”还是“证据不足”；
- 下一步是什么，以及“不会自动进入 A 池/不会自动下单”。

## 权威入口

- 研究状态：`GET /api/lab/research-status`
- 当前 V2 七闸门：`GET /api/v2/readiness`
- 总验收记录：[ACCEPTANCE-V2-REMEDIATION-FINAL.md](./ACCEPTANCE-V2-REMEDIATION-FINAL.md)
- 任务事实源：`tasks/implementation_state.yaml`
