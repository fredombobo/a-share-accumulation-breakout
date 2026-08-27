# V2 最终闭环工作包（2026-08-27）

## 目标

在 `accumulation_breakout` 当前代码身份上关闭以下阻断项：

1. 供应商 HTTPS/TLS 传输及 fail-closed 安全证据；
2. 当前代码身份、当前配置和当前数据库的权威研究运行；
3. 生产扫描信号观察、可成熟 outcome 与组合风险快照；
4. 最新完成交易日的扫描、纸面周期、对账、审计锚定与 COMPLETE 日清单；
5. 至少 7 份可验证、可恢复、异位置备份；
6. 从接受后的真实完成交易日起累计 5 个不同交易日 soak。

五日 soak 是时间型验收条件。本工作包只实现自动采集、身份校验和失败阻断；不得用历史回填、修改时间戳或纸面周期行数冒充真实 soak。

## 实施顺序与影响文件

### A. 安全数据通道

- 修改 `tushare_init.py` 与本机 `.env`：默认和生产配置仅允许 HTTPS 自定义节点。
- 新增传输探针和结构化证据生成脚本；证书验证失败、HTTP 回退或接口不可用均失败。
- 修改治理门禁生成逻辑及相应测试、部署文档。

### B. 生产闭环接线

- 修改 `ab_screener/operations/dag.py`：在日清中持久化真实组合风险快照。
- 复用 `paper_trading/risk_adapter.py`、`portfolio_risk.py` 和 `risk_repository.py`，不复制前端公式。
- 新增 EOD 运维命令，串联交易日核对、扫描、DAG、对账、审计锚定、备份与 soak 采集。
- 开启策略观察注册，但所有实验策略继续保持 `EXPERIMENTAL`，不自动进入 A 池或下单。

### C. 研究与身份

- 在代码修复、提交和服务重启后启动固定预注册参数的真实权威研究。
- 研究结果如实记录 PASS/FAIL/INSUFFICIENT；失败不改写为通过。
- 将当前运行 ID 写入 `configs/platform_v2.yaml`，并重新生成与最终身份匹配的门禁证据。

### D. 备份、恢复与 soak

- 扩展 `ab_screener/operations/backup.py` 和 `scripts/restore_backup.ps1`，支持压缩备份、不可变清单、SHA-256、完整性验证和实际恢复。
- 保留至少 7 份最近成功备份；任何未验证或不可恢复文件不计数。
- 每个真实 COMPLETE 交易日只追加一份身份绑定的 soak 证据；第五个日期前 O 门禁保持 `INSUFFICIENT`。

## 验收

- HTTPS API 实际请求成功，证书验证开启，配置中不存在生产 HTTP 回退。
- 权威研究记录的代码、配置、数据集和成本版本齐全，报告可复算，结论不被篡改。
- `signal_observations > 0`；成熟但不可成交的 outcome 保持 `UNFILLABLE/NULL`，不得伪造收益。
- 最新完成交易日存在 `risk_snapshots`、`dag_runs.COMPLETED`、对账 OK、审计链/外部锚点有效及 `daily_run_manifests.COMPLETE`。
- 7 份备份均有清单、内容哈希和验证时间；严格恢复到新临时目标并通过完整性检查，RTO 不超过 30 分钟。
- 5 个不同真实完成交易日 soak 才可通过 O-12；不足时 readiness 必须诚实阻断。
- `LIVE_TRADING_ENABLED=false` 始终不变。
- 相关 Pytest、Ruff、Mypy、前端构建和最终 `scripts/quality_gate.ps1 -Strict` 通过；任务状态、状态页、变更日志和回滚说明同步。

## 回滚

- 关闭新增调度/策略观察旗标即可停止生产写入。
- 数据表只追加；风险、研究、审计、日清和 soak 证据不删除。
- 配置回退前先保留最终配置哈希；数据库恢复只允许到新路径，绝不覆盖运行中的生产库。

## 2026-08-28 当前身份进度

| 项目 | 结果 | 证据 |
|---|---|---|
| HTTPS 实际数据访问 | PASS | 真实数据门禁使用 HTTPS、源端 100 对抽样零差异；无 HTTP 回退。 |
| 独立原生 TLS 探针 | PASS | `2026-08-28T03:37:04+08:00` 复测通过；证据 SHA-256 `7e2dd1badb6b...`，无 HTTP 回退。 |
| 权威研究 | 完成但 FAIL | `v2auth20260828f`，代码 `d5707c22c645`；OOS 组合净收益 8.49%、PF 1.223、回撤 9.18%，但 PBO 31.25%、DSR 46.36%、MinTRL 覆盖 18.8%、嵌套窗 1/5 为正。 |
| 生产策略观察 | 已接线、成熟度不足 | 52 条 SHADOW、5 类策略、0 条成熟 outcome。 |
| 风险/日清 | PASS | 20260827 风险快照、DAG、周期、对账、COMPLETE 清单齐全。 |
| 严格恢复 | PASS | 16.5GB，双 SHA/完整性/FK 通过，1789.798/1800 秒。 |
| 备份 | 5/7 | 不用同日重复文件伪造连续恢复点。 |
| soak | 1/5 | 仅 20260827 计数，仍需 4 个真实完成交易日。 |
| 实盘开关 | 关闭 | `LIVE_TRADING_ENABLED=false`。 |

当前 P8 仍为 `BLOCKED`；剩余条件包含真实时间和策略本身失败，不能在一次会话中伪造关闭。
