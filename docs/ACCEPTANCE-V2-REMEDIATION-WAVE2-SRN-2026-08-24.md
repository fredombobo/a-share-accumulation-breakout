# V2 修复计划第二波（S / R / N）管理者终验

日期：2026-08-24（Asia/Shanghai）  
集成分支：`v2r-wave2-integration`  
共同基线：`7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe`

## 1. 裁决

本次已提交的第二波三个任务全部 **ACCEPTED**：

| 任务 | 工程验收 | 业务结论 | 说明 |
|---|---|---|---|
| V2R-S | ACCEPTED | 默认关闭，允许进入 O2 接线 | 扫描信号、成交驱动 ENTERED、outcome 与重放幂等已闭合 |
| V2R-R | ACCEPTED | 策略研究 `FAIL`，不得晋级 | 工程任务目标是生成可信证据；诚实 FAIL 不等于任务失败 |
| V2R-N | ACCEPTED | 只读信息覆盖层，默认关闭 | 不参与 A/B 池、仓位、订单或真实交易 |

第二波完整出口尚未完成：`V2R-O2` 此前因依赖 S 被阻塞，现在依赖已经满足，状态改为 `ready`。程序总状态继续为 `BLOCKED`，不得宣布 `PERSONAL_INSTITUTIONAL_READY`。

## 2. 管理者发现并直接修复的问题

### V2R-S

初版不能通过，复验发现：

1. `V2_STRATEGY_REGISTRY_ENABLED=true` 仍被解析为关闭，生产 hook 实际不可达；
2. 信号输入哈希只含版本和代码，K 线变化不会形成新修订；
3. 重放已有 observation 时不重新生成 A 池资格；
4. outcome 幂等键遗漏基准超额收益，且非法日期可被字符串比较接受；
5. 生产 hook 静默吞异常，无法观察部分失败。

修复后：配置读取使用完整 resolved config；输入哈希覆盖规范化 K 线、版本、代码和 as-of；资格从本次完整 observation 集合派生；日期严格规范化；基准修订会追加 outcome；异常会记录部分/整体失败。新增的五个对抗测试均先复现失败，再在修复后通过。

权威源分支 HEAD：`a07b8cfacb896d6e48dbd597ba6c57b50284e61e`。

### V2R-R

初版证据存在阻断级未来函数。旧任务 `1699927499ff` 中，434 笔 OOS 成交有 175 笔（40.32%）的入场日早于记录信号日。根因是优化器先读取突破后的站稳/回踩数据确认形态，再把信号和进场回填到突破日及次日。

管理者修复为：

- 每个候选必须用仅截止突破日的 causal window 重新检测；
- 重新检测必须得到相同突破日；
- 基准量只由 causal window 计算；
- 信号日期固定为突破日，另保留 `discovered_on`；
- 对 `(code, breakout_date)` 去重；
- 研究窗口边界外的回填信号拒绝。

旧任务和旧数字全部作废但保留审计记录。唯一有效冻结任务为 `0746a4108e15`：

- 数据：979 个交易日、600 股、step=5、完整 54 组网格；
- IS：2023-08-01 至 2025-07-31；OOS：2025-08-01 至 2026-07-31；
- OOS：422 笔净成交，净 PF `1.112`、胜率 `41.0%`、最大回撤 `88.03%`；
- WF 净 PF：`0.613 / 0.730 / 1.240`，只有 1/3 盈利；
- 随机基线净均收益 `-0.5361%`，MA20/60 基线 `-0.2217%`；
- 成本压力：1× PF `1.112`、2× `0.986`、3× `0.875`；
- PBO `0.3815`（FAIL），DSR `0`，MinTRL `1762.49`；
- 最小 ADV20 约 2657.85 万元，5% 参与率下单日容量约 132.89 万元；
- 门禁：`verdict=FAIL`、`candidate_eligible=false`。

独立回放验证：数据集指纹与冻结版本相同，回放指标与报告相同；入场不晚于信号、信号/突破日错位、同标的同突破日重复三项计数均为 0。未登记候选、未进入 A 池、未生成订单。

证据目录：`runtime/v2/research_v2r_r/evidence/`。最终 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `evidence_package.json` | `1d83722c1a223ed85a10b2efd9405bbd93f31383ce280f6ba5941db473488293` |
| `evidence_package.md` | `2de2692d6d42d30e7660c032ad29bffa9068b116b6bea2eaa95e7da01e4858ba` |
| `pbo_matrix.json` | `d7c91463424b46bc658adb411f66ffa10c4800e6b83d703ae8354fd2ed5a988b` |
| `verify_metrics.json` | `513a68c3d7ee5f90d4834906450e4beccee645808aadec18b9ddc4bedf878ed3` |

`sha256_manifest.json` 经独立复算与以上四项完全匹配。源分支最终 HEAD：`269ec71c58cb9e3aca36ad1000cf7731c5402d60`。

### V2R-N

初版在信息不足时没有严格 fail-closed，并改变了被注释决策对象的返回形状。复验发现：缺 permission 可被接受、缺 evidence refs 会伪造空列表、本地抓取时间默认等于供应商可用时间、未知 overlay 被静默忽略、混合结果可能误报 PASS、`annotate_decision` 会把原对象整体移入 `decision`。

修复后：缺权限/证据引用/未知覆盖层均返回结构化 `INSUFFICIENT`；任何覆盖层不足都会使总状态不足；`ingested_at` 默认使用真实当前 +08:00 时点；注释仅新增顶层 annotations/disclaimer，不改变原决策字段。覆盖层继续默认关闭且只读。

权威源分支 HEAD：`cac52559cba48cdaacc4da672b72f18221d71d8a`。

## 3. 集成与自动化证据

使用权威 Python 3.12：`E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`。

| 检查 | 结果 |
|---|---|
| S/R/N 相关定向 Pytest | `139 passed in 34.56s` |
| 集成全量 Pytest | `825 passed, 8 warnings in 197.67s` |
| 第二波全部新增/修改 Python 文件 Ruff | PASS |
| 第二波相关 27 个源码文件 Mypy | PASS |
| `optimizer.py` 单文件 Mypy | PASS |
| `scripts/check_architecture.py --strict` | PASS |
| 前端 `npm run build` | PASS；仅 ECharts 687.37 kB 性能警告 |
| `git diff --check` | 第二波代码无空白错误；启动包 EOF 空行已在管理提交清理 |
| 凭据/实盘能力扫描 | 未发现 Token、实盘开关开启或券商下单接线 |

全量测试有 8 条非失败警告，其中一条是 Windows 子进程输出按 GBK 解码触发的线程警告，另有 pandas FutureWarning。它们进入 Q2 债务清单，不应被描述为零警告。

## 4. 证据边界

- **本地已观测事实**：以上测试、冻结任务、回放、报告哈希和本地历史数据库结果。
- **研究证据**：`0746a4108e15` 只证明当前冻结策略在该数据、成本和容量假设下不应晋级；不代表未来收益，也不是生产数据门禁 PASS。
- **生产状态**：本轮未运行外部真实数据门禁、未启用 scheduler、未完成五个真实交易日 soak，也没有任何实盘能力。因此程序仍为 `BLOCKED`。

## 5. 尚未通过的全局质量门

`scripts/quality_gate.ps1` 目前不能宣称全绿：

- 全仓 Ruff：88 errors；第二波差异内已为 0，剩余是集成基线债务；
- 质量脚本 Mypy 固定文件集：9 errors；本轮曾新增的一处 dedupe key 类型错误已修复，剩余 9 条位于未改动的基线行；
- Pytest 虽全部通过，但仍有 8 warnings；
- ECharts chunk 超过 500 kB，仅是性能警告。

这些项目由依赖 O2 的 `V2R-Q2` 统一清零，避免 O2 合并后重复清理。禁止通过扩大 ignore/exclude、删测试或放宽领域语义制造绿灯。

## 6. 下一执行批次

立即释放 `V2R-O2`：持久 EOD DAG、故障恢复、审计链和真实五交易日 soak 启动。它必须先在数据库副本上故障注入和重放，`DAILY_SCHEDULER_ENABLED` 继续为 false；五日证据从接受后的真实完成交易日起累计，未满五日时 O 闸门保持 BLOCKED。

O2 经管理者验收后，再释放 `V2R-Q2`；随后依次为 `V2R-G` 和管理者独立 `V2R-P8`。

## 7. 回滚

- S 的生产持久化受 `V2_STRATEGY_REGISTRY_ENABLED` 控制，默认 false；关闭即可恢复 no-op，不删除已追加记录。
- N 配置 `enabled: false`，且没有订单/持仓写路径；回滚只需不调用覆盖层或 revert 对应提交。
- R 只改变研究检测语义和离线证据，不改变纸面账本；禁止恢复引用旧非因果报告。
- 所有回滚均使用追加冲正或 commit revert；不得删除生产数据库、历史信号、研究失败证据或审计记录。

