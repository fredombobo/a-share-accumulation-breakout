# 项目状态看板

> 更新：2026-08-27 · V2 remediation 独立总验收

## 当前结论

当前项目是 `accumulation_breakout`（**AB-Screener · 横盘吸筹突破**），本机入口为
`http://127.0.0.1:8001/`。它不是 AETF Alpha；AETF/8000 不属于本轮交付。

三波工程任务已经完成，代码、数据同步和前端回归均通过；但七闸门的现场裁决仍为
**BLOCKED**，不能宣称“个人机构级就绪”或“实盘就绪”。当前只有数据/PIT 闸门 D 为 PASS，
研究、策略、风险、日清、运维和治理仍存在真实证据缺口或硬失败。

`LIVE_TRADING_ENABLED=false`，系统仅研究和纸面仿真，不连接券商、不产生真实订单。

## 七闸门状态

| 闸门 | 状态 | 当前事实 |
|---|---|---|
| D 数据/PIT | **PASS** | 968 个完成交易日；最新日 20260826；20×5 源端抽样零差异；daily/daily_basic/moneyflow canonical 与 PIT 最新分区一致。 |
| R 研究 | **INSUFFICIENT** | 生产事实库没有当前代码身份的权威研究；历史实验结论为 FAIL，不能晋级。 |
| S 策略/信号 | **INSUFFICIENT** | 六插件契约已完成，但生产库尚无策略档案、信号观察和 outcome 样本。 |
| P 组合/风险 | **INSUFFICIENT** | 风险实现和 fixtures 通过，生产库仍无正式风险快照。 |
| L 账本/日清 | **FAIL** | 最新日清清单为 20260821 `PARTIAL`，阻断原因为 `MISSING_SCAN_RUN`；20260826 无完整日清证据。 |
| O 运维/恢复 | **FAIL** | 可验证备份 3/7；当前身份真实交易日 soak 0/5。 |
| G 治理/安全 | **FAIL** | 实盘关闭、身份与前端已通过；数据供应商节点仍是明文 HTTP，生产审计链/外部锚点证据不足。 |

权威现场状态由 `GET /api/v2/readiness` 返回。客户端不能通过 URL、localStorage 或请求参数
把失败闸门改为通过。

## 本轮已经完成

- 统一服务身份、服务端 flags、readiness 和 8001 入口。
- canonical 行情与 append-only PIT history 原子双写；复核最近五个完成交易日。
- 修复整数/REAL 表示差异导致的伪 revision；相同数据重跑零新增修订。
- 修复迁移 checksum 对 worktree 绝对路径敏感的问题，并在 16GB 副本与生产库重复验证。
- 平台状态接口返回部分 payload 时，前端 fail-closed，不再整页崩溃。
- 完成严格质量门、全量测试、PIT 专项、前端单测、Playwright 和 npm audit。

## 当前质量证据

| 检查 | 结果 |
|---|---|
| `scripts/quality_gate.ps1 -Strict` | PASS：Ruff、Mypy、架构边界、868 个离线测试、前端构建 |
| 全量 Pytest | **900 passed** |
| PIT/数据专项 | **70 passed** |
| Vitest | **7 passed** |
| Playwright | **4/4 passed**（导航、刷新恢复、390px、键盘焦点） |
| npm audit | **0 vulnerabilities** |

Python 发布证据必须使用 `.venv312`（Python 3.12），不得用裸 `python` 或 3.14 结果替代。

## 下一步（按阻断顺序）

1. 把供应商自定义节点置于 HTTPS 或可信 TLS 隧道后，重跑 G 门禁。
2. 以当前代码、配置、数据、成本和撮合身份预登记并运行权威研究；允许诚实 FAIL。
3. 注册正式策略档案，积累 Shadow/Paper 信号 outcome 和生产风险快照。
4. 完成最新交易日扫描、纸面周期、风险、对账与 `daily_run_manifest COMPLETE`。
5. 累积 7 份验证备份并完成严格恢复；按真实交易日累计 5/5 soak。
6. 生成生产写操作审计链和数据库外 chain-head 锚点，再进行 P8 复验。

## 文档索引

- [V2 remediation 独立总验收](./ACCEPTANCE-V2-REMEDIATION-FINAL.md)
- [研究路线图](./RESEARCH-ROADMAP.md)
- [小白使用手册](./小白使用手册.md)
- [操作手册](./操作手册.md)
- [V2 API 契约](./API-CONTRACT-V2.md)
