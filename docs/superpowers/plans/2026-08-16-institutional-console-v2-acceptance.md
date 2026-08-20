# 个人机构化研究与纸面交易平台 v2.0 验收矩阵

| 字段 | 内容 |
|---|---|
| 文档 ID | `PERSONAL-INSTITUTIONAL-CONSOLE-V2-ACCEPTANCE` |
| 状态 | **验收口径冻结；待实现和留证** |
| 适用版本 | v2.0 |
| 事实日期 | 2026-08-16 |
| 最终状态 | `PERSONAL_INSTITUTIONAL_READY` 或明确阻断状态 |

## 1. 统一判定协议

每个检查必须输出结构化记录：

```json
{
  "check_id": "R-PBO",
  "metric": "probability_of_backtest_overfitting",
  "operator": "<=",
  "threshold": "0.20",
  "observed": "0.40",
  "status": "FAIL",
  "reason_code": "PBO_ABOVE_LIMIT",
  "source_refs": [
    {
      "path": "runtime/v2/research/<run_id>/anti_overfit.json",
      "json_pointer": "/pbo",
      "sha256": "..."
    }
  ]
}
```

状态只能是：

- `PASS`：字段齐全、证据有效、身份一致且指标满足；
- `FAIL`：明确违反阈值、身份、时点、安全或不可变约束；
- `INSUFFICIENT`：未运行、字段缺失、样本不足或无法计算；绝不能当作 PASS。

聚合优先级固定：

```text
FAIL > INSUFFICIENT > PASS
```

平均分和旧机构/商业评分仅可作为诊断，不能抵消任一硬闸门失败。

能力布尔值独立计算：`research_ready = D/R/S/G 全 PASS`，`paper_engine_ready = D/S/P/L/O/G 全 PASS`。互斥总状态按固定顺序计算：

1. 七闸门全 PASS → `PERSONAL_INSTITUTIONAL_READY`；
2. D/S/P/L/O/G 全 PASS 且 R 非 PASS → `ENGINEERING_READY_RESEARCH_BLOCKED`；
3. D/R/S/G 全 PASS → `RESEARCH_READY`；
4. 其他 → `BLOCKED`。

每个闸门报告必须符合：

```json
{
  "schema": "personal-institutional-gate-v2",
  "gate_id": "R_RESEARCH",
  "gate_version": "2.0.0",
  "status": "FAIL",
  "checked_at": "2026-08-16T21:00:00+08:00",
  "valid_until": "2026-09-11T21:00:00+08:00",
  "code_version": "...",
  "worktree_dirty": false,
  "resolved_config_hash": "...",
  "data_fingerprint": "...",
  "database_fingerprint": "...",
  "source_refs": [],
  "checks": [],
  "blockers": [],
  "failure_injections": [],
  "artifact_sha256": "..."
}
```

证据 JSON 使用版本化 canonical JSON（UTF-8、键排序、确定性 Decimal 字符串、无非语义空白）。`artifact_sha256` 计算时排除该字段自身；父级 evidence index 必须读取七份子报告原始字节重新计算 SHA-256，并验证每个 `source_ref`，不能只相信子报告中的 status。研究产物绑定冻结的 experiment dataset manifest，不绑定持续变化的整库文件哈希。示例 `valid_until` 仅表示身份不变时的最迟复验点；任何代码、配置、数据集、成本或撮合身份变化都会让 R 证据立即失效。

各闸门有效期分别定义：

| 闸门 | 有效期 |
|---|---|
| D 行情运行证据 | 至下一个已完成交易日；真实数据发布报告 24h |
| R 研究产物 | strategy/code/config/dataset/cost/fill identity 变化或满20个新交易日后失效 |
| S 插件契约 | plugin/ENTRY/config 变化即失效；signal/outcome 按交易日更新 |
| P 风险算法 fixture | 算法/config identity 变化即失效；组合快照至下一交易日 |
| L 日清单 | 每个完成交易日一份；任何冲正/公司行为使对应清单失效 |
| O 健康/备份/观察 | 健康5分钟，备份24h，最近5个交易日连续观察 |
| G 发布总索引 | 24h，任一代码/config/data/schema/报告身份变化立即失效 |

## 2. 当前基线状态

这是实施前诊断，不是最终验收结果：

| 闸门 | 当前判断 | 主要阻断 |
|---|---|---|
| D 数据/PIT | FAIL | 非日线数据未形成完整 PIT 修订链；本地974日与签名门禁968日身份未统一 |
| R 研究 | FAIL | AB 最近600股实验明确 FAIL；父级 PBO/DSR 仅为 legacy diagnostic，尚无身份连接 |
| S 信号/策略 | INSUFFICIENT | 多形态插件、不可变 signal observation 和 lifecycle 尚无机器 inventory 证据 |
| P 组合/风险 | INSUFFICIENT | 行业/主题/相关暴露、VaR/CVaR、压力情景尚无完整机器证据 |
| L 账本/对账 | FAIL | 最新 `daily_run_manifests.status=PARTIAL` 且 `blockers` 含 `MISSING_SCAN_RUN`；settlement 仍有 `INSERT OR REPLACE`。`PARTIAL` 是业务字段值，不进入 gate enum |
| O 运维/恢复 | FAIL | 统一 DAG、备份恢复、同步时间语义和连续观察证据缺失 |
| G 治理/安全 | FAIL | WORKTREE_DIRTY、CODE_VERSION_MISMATCH、明文供应商通道、旧报告乱码/老化和审计缺口 |

完成工程代码不会自动改变 R 闸门；只有新的真实研究证据达到阈值才可 PASS。

当前互斥总状态必须为 `BLOCKED`。只有 D/S/P/L/O/G 全 PASS 且仅 R 非 PASS 时，才可使用 `ENGINEERING_READY_RESEARCH_BLOCKED`；任何安全、数据、账本或运维失败都不得使用该状态。

## 3. D：数据与 Point-in-Time 闸门

| ID | 检查 | PASS 阈值 |
|---|---|---|
| D-01 | 交易日覆盖 | ≥730 个完成交易日 |
| D-02 | 最新日期 | 本地、源端和基准最新完成交易日一致 |
| D-03 | 活跃标的覆盖 | ≥98% |
| D-04 | 关键标的覆盖 | 持仓、活动订单、A池候选 100% |
| D-05 | 数据合法性 | 重复键、非法 OHLC、负量额均为 0 |
| D-06 | PIT 元数据 | 所有信号输入五元组完整率 100% |
| D-07 | As-of 可见性 | 所有读取满足 `available_at <= decision_at` |
| D-08 | 历史宇宙 | 上市前/退市后/错误品种混入数量为 0 |
| D-09 | 源端抽样 | 固定种子 ≥20标的×5日，源精度内差异为 0 |
| D-10 | 可复现快照 | 同 snapshot 重跑数据和特征 SHA-256 完全一致 |
| D-11 | 情报数据集 | daily_basic/moneyflow/fina/adj/company-action/industry/index等逐类 capability、覆盖、新鲜度和PIT抽样通过 |
| D-12 | 同源查询 | 信息页、扫描、回测对同 snapshot 的公共字段/哈希一致 |
| D-13 | 原始归档 | 正式输入均可追溯 raw manifest；文件hash、原子写和保留规则通过 |

**必需证据**

- `runtime/gates/real_data_gate_*.json`
- `runtime/v2/gates/data.json`
- `raw_ingest_manifests`、dataset/PIT universe manifest
- `/api/health`、`/api/paper/gates/status`
- [数据源与 PIT 合同](../specs/2026-08-16-institutional-console-v2-data-contract.md)定义的逐dataset capability/quality报告

**有效期**

- 真实数据发布报告：24 小时；
- 行情运行证据：至下一个完成交易日；
- 代码、配置、schema 或数据库变化：立即失效。

**失败注入**

1. 插入一条 `high < low`；
2. 制造重复业务键；
3. 将 `available_at` 改至决策之后；
4. 注入上市前股票、退市后股票和指数代码；
5. 修改代码后复用旧 gate；
6. 无 Token 运行真实门禁。

所有注入必须在 disposable DB 副本和临时输出根目录执行，生产数据库 fingerprint 前后必须一致。无 Token 时 D 检查状态固定为 `INSUFFICIENT`、进程退出码非0；发布聚合因 D 非 PASS 而得到 `BLOCKED`。

## 4. R：研究可信度闸门

默认候选 profile：`ROBUST_PERSONAL_V2`。它不是现有 `institutional_98` 的 strict 同义词；可选严格对照 `STRICT_RESEARCH_V2` 使用 PBO <10%、DSR >95%、MinTRL ≥1，禁止使用 `STRICT_PERSONAL` 别名。

| ID | 检查 | PASS 阈值 |
|---|---|---|
| R-01 | 实验登记 | 正式运行前完整且不可变 |
| R-02 | 数据模式 | `research_mode=full` 且数据 ≥730日 |
| R-03 | 独立 OOS | 净收益 >0，并优于预登记主要基线 |
| R-04 | Nested WF | ≥5 个有效外层测试窗，正收益窗 ≥60% |
| R-05 | Nested OOS Sharpe | >0 |
| R-06 | PBO | ≤20% |
| R-07 | DSR | ≥95% |
| R-08 | MinTRL coverage | ≥1.0 |
| R-09 | 成本压力 | 2×成本下净OOS>0且对主基线超额>0；3×完整披露 |
| R-10 | 容量压力 | 1/2/5/10%参与率均报告；候选容量以2%为参考，纸面硬上限另由风险profile控制 |
| R-11 | 参数稳定 | 预登记邻域中 ≥60% 参数组合净OOS与主基线超额同为正 |
| R-12 | 多重试验 | 策略家族和全部 trials 计入修正，失败试验不得删除 |
| R-13 | 基线 | 随机、MA20/60及预登记主基线均有净成本结果 |
| R-14 | 产物完整 | code/config/data/entry/cost/fill/plugin hashes 全部匹配 |

**结论状态**

- `CANDIDATE`：R 闸门全部 PASS，只允许进入影子观察；
- `REJECTED`：存在明确统计失败；
- `INSUFFICIENT_EVIDENCE`：样本或字段不足。

PASS 不得自动进入 A 池、生成订单或修改生产配置。

**必需证据**

- `runtime/v2/gates/research.json`
- 实验 registration、trial ledger、Nested WF 产物
- PBO/DSR/MinTRL 计算输入和输出
- baseline、cost stress、capacity、neighbor stability 报告
- artifact manifest 与 SHA-256

**失败注入**

- 用 OOS 选择参数；删除失败 trials；破坏折叠日期；降低样本至 MinTRL 以下；令 PBO>20%、DSR<95%；篡改报告；在成本模型变化后复用旧报告。

父级旧证据只能标记 `legacy_advisor_diagnostic`：PBO 0.40、DSR 0.5466、MinTRL 3.3218、Nested OOS Sharpe -0.127。只有 strategy/plugin、ENTRY semantic hash、dataset manifest、trial ledger、cost/fill model、code/config 全匹配后才可进入 AB 的 R 闸门；当前 AB 自身600股实验已足以使 R=FAIL。

## 5. S：策略插件与信号治理闸门

这里的 Live Shadow 指按真实交易日向前积累、但不创建订单的只读观察，不是实盘交易或券商连接。

| ID | 检查 | PASS 阈值 |
|---|---|---|
| S-01 | 插件契约 | 六个首批插件全部通过 schema/类型契约 |
| S-02 | 经济定义 | 每个插件有假设、时点、公式、失效条件和持有期 |
| S-03 | 插件 PIT | 决策后数据可见性违规为 0 |
| S-04 | 版本兼容 | V1 golden 完全不变；新语义使用新 ID/hash |
| S-05 | 扫描幂等 | 同 input hash 重跑 signal observation 无重复 |
| S-06 | 漏斗守恒 | A/B 分支集合均为上游子集，伪造计数为 0 |
| S-07 | 生命周期 | 非法转换为 0；`ENTERED` 只由实际 fill 触发 |
| S-08 | Outcome 时点 | 5/10/20日结果只在成熟且 available 后回填 |
| S-09 | 插件隔离 | 单插件异常不拖垮其他插件，并有失败运行记录 |
| S-10 | 组合信号 | 未预登记的跨插件加分/挑优路径为 0 |
| S-11 | Shadow 成熟度 | 同一 plugin/identity 成熟样本 ≥30、时间 ≥3个月、outcome完整率 ≥98% |
| S-12 | Shadow 质量 | 相对沪深300和全A平均超额均 >0，双基准t统计均 ≥1.96 |
| S-13 | Paper 长期支持 | 同一 identity 成熟样本 ≥300、时间 ≥12个月，双基准超额为正 |
| S-14 | Live/Paper 漂移 | 方向一致；关键超额指标衰减不超过50%，并报告版本化分布漂移指标 |
| S-15 | 可用策略底线 | 至少一个插件在同一 identity 下通过 R、Shadow、Paper、数据和风险检查并成为 `ACTIVE_FOR_A_POOL`；其余插件可保持 EXPERIMENTAL/REJECTED |

**必需证据**

- strategy registry/version manifest
- scan profile、funnel DAG、signal observations/events/projection/outcomes
- plugin golden fixtures 和独立研究状态
- 与 plugin/ENTRY/dataset/cost/fill identity 相连的 shadow/paper validation 和 drift manifest

**失败注入**

- 插件读取未来修订；重复运行；两个 profile 同标的同日；将 CONFIRMED 直接改 ENTERED；提前写 outcome；抛出单插件异常；尝试将实验插件放入 A 池；截断 Shadow 样本/月份；删除一个基准；制造 outcome 重复或严重漂移。

父级旧 Shadow/Paper 数据在 identity 连接前只作诊断。其 Shadow 221个/3.5个月已满足成熟度，但双基准 t 统计约1.71/1.73，按 S-12 仍失败；“成熟”不得显示为“质量通过”。

## 6. P：组合与风险闸门

默认保守 profile：

- 单标的 ≤10%；总持仓 ≤80%；现金 ≥10%；单日新增 ≤20%；
- 单行业/主题 ≤25%；高相关资产组 ≤30%；参与率 ≤5%。

| ID | 检查 | PASS 阈值 |
|---|---|---|
| P-01 | 约束单一事实源 | review、confirm、撮合前复检使用同一版本引擎 |
| P-02 | 拒绝码 | 每个硬约束有稳定独立拒绝码和边界测试 |
| P-03 | 暴露守恒 | 含现金权重合计 `1 ± 1e-8` |
| P-04 | 风险指标 | TWR、波动、Sharpe、DD、VaR95、CVaR95 手算误差 <1bp |
| P-05 | 样本不足 | 返回 `INSUFFICIENT`，不得以 0 表示安全 |
| P-06 | PIT 风险快照 | 未来行情不能改变历史快照 |
| P-07 | 压力情景 | 指数-5%、行业-10%、相关下跌、流动性腰斩、成本2/3×全部可复算 |
| P-08 | 并发预留 | 同一订单并发确认只产生一次现金/份额预留 |
| P-09 | 防守/陈旧状态 | 明确阻断新增买入，不误拦风险降低型卖出 |

**必需证据**：risk config version、exposure/risk/stress snapshots、订单 review/confirm checks、手算 fixtures。

**失败注入**：现金不足、超单票/行业/相关组、T+1、行情过期、参与率超限、缺估值、基准缺失、并发确认、卖出时 max-position 误判。

## 7. L：纸面账本、撮合与对账闸门

| ID | 检查 | PASS 阈值 |
|---|---|---|
| L-01 | 定点精度 | 现金与费用误差为 0 分；无浮点账务写入 |
| L-02 | 执行一致 | 同 fixture 的 canonical business hash 完全一致；排除ID、generated_at和审计envelope |
| L-03 | 零/部分成交 | 停牌、零量、无open、涨跌停、容量不足规则全部覆盖 |
| L-04 | 时间语义 | 收盘信号不得同收盘成交；T+1/sellable_at 正确 |
| L-05 | 订单数量 | 累计成交不超过订单数量 |
| L-06 | 现金 | 现金流水汇总等于余额，误差 0 分且不为负 |
| L-07 | 持仓 | 批次汇总等于快照，不为负，可卖不超过总量 |
| L-08 | 总资产 | 现金+持仓市值严格等于总资产，误差 0 分 |
| L-09 | 行情/公司行为 | 成交行情版本存在；未处理公司行为为 0 |
| L-10 | 幂等 | 同 key 同请求返回原结果；同 key 异请求返回 409 |
| L-11 | 不可变 | 账本无 `INSERT OR REPLACE`；更正只追加冲正 |
| L-12 | 日清单 | 最新完成交易日 cycle DONE、reconciliation OK、manifest COMPLETE |

**失败注入**

- 1分钱现金差异、1股份额差异、重复成交、负现金/负持仓、超卖、缺行情版本、未处理公司行为、同 key 异 payload、无 open 回退 close。

任何注入都必须使日结 BLOCKED，且不得生成下一日买入草稿。

## 8. O：运维、调度与恢复闸门

| ID | 检查 | PASS 阈值 |
|---|---|---|
| O-01 | DAG 顺序 | 与设计规格唯一 DAG 完全一致 |
| O-02 | 步骤幂等 | 同日/账户/profile/step/input hash 最多成功一次 |
| O-03 | 崩溃恢复 | 重启从最后 checkpoint 续跑，无重复副作用 |
| O-04 | 重试 | `max_attempts=3`（含首次执行），保留所有 attempt 和原因 |
| O-05 | 租约 | 多实例/重复启动只有一个 owner，过期租约可安全接管 |
| O-06 | Windows 文件竞态 | Access Denied 注入后重试或明确 FAILED，无永久 RUNNING |
| O-07 | 备份新鲜度 | 最近验证成功备份 <24h，至少连续7份 |
| O-08 | 恢复 | 实际临时库恢复、foreign key/关键表 hash 一致，RTO ≤30分钟 |
| O-09 | RPO | ≤1个交易日 |
| O-10 | 健康状态 | build/config/data/DB/DAG/磁盘/备份/端口身份齐全 |
| O-11 | 持仓同步 | 成功时间只在真实成功时更新；旧缓存明确 stale |
| O-12 | 连续观察 | 至少5个交易日无人值守，无重复任务、丢单或未收口 RUNNING |

**持仓同步字段**

`poll_attempted_at`、`source_snapshot_at`、`last_successful_sync_at`、`cache_restored_at` 和 `updated_at` 必须分离。失败轮询不得更新成功同步时间。

**失败注入**

- 杀掉扫描/研究/日结进程；重复触发调度；占用租约；模拟网络超时、磁盘不足、端口冲突、损坏最新备份和进度文件 Access Denied。

## 9. G：治理、安全、API 与用户体验闸门

| ID | 检查 | PASS 阈值 |
|---|---|---|
| G-01 | 发布身份 | clean Git；代码/config/data/DB/report hashes 与运行实例一致 |
| G-02 | 证据有效期 | 发布/真实数据/总证据报告 ≤24h |
| G-03 | 全站审计 | OpenAPI枚举范围内写操作及后台写入的审计覆盖率 100% |
| G-04 | 审计防篡改 | append-only/hash chain；每日chain head签名并锚定至DB外备份根，可检测局部篡改 |
| G-05 | 敏感信息 | 代码、日志、报告和前端 Token/账户敏感值命中为 0 |
| G-06 | 实盘隔离 | `LIVE_TRADING_ENABLED=false`；无券商下单 adapter |
| G-07 | 区域隔离 | Lab/Review 无下单动作；研究 PASS 不自动产生 A 池/订单 |
| G-08 | API 契约 | OpenAPI、TS 类型、错误码、幂等和分页契约全部通过 |
| G-09 | 报告编码 | UTF-8，无乱码；引用 evidence SHA-256 |
| G-10 | UI 可用 | 桌面+390px、键盘、焦点、全部按钮、网络/空/旧缓存状态 E2E 通过 |
| G-11 | 状态恢复 | 切页、失焦、刷新和服务恢复后任务与结果从服务端恢复 |
| G-12 | 端口身份 | AB 8001/开发3001 与 AETF 8000 不串台 |
| G-13 | 本机网络 | 仅绑定 127.0.0.1；CORS/Origin/写请求保护通过 |
| G-14 | 明文数据通道 | 未采用 TLS/可信隧道时明确 `SECURITY_DEGRADED`，不得全绿 |

旧机构 86 分、商业 88 分不得作为 v2 硬门或最终通过条件。v2 只报告七闸门及其证据；可选评分必须能从检查逐项复算。

G-03 的分母为 OpenAPI 中所有 POST/PUT/PATCH/DELETE（明确列入 allowlist 的纯只读 POST 除外）以及所有 scheduler/internal write use case；每项必须关联 user request 或 DAG correlation ID。G-04 的威胁模型仅承诺检测数据库局部删除、改写和断链，不宣称能抵抗已取得主机管理员权限且同时控制签名密钥与外部锚点的攻击者。

## 10. 性能验收

在固定机器、固定数据库快照和固定并发下记录 p50/p95/p99，冷/热分别报告：

| 场景 | 阈值 |
|---|---:|
| `/api/health` 热 p95 | <200ms |
| Desk 热 p95 | <500ms |
| Overview 100候选热 p95 | <500ms |
| Overview 100候选冷 p95 | <2.5s |
| Overview 100候选响应 | <300KB |
| 100持仓摘要 p95 | <500ms |
| 1000订单分页 p95 | <500ms |
| 扫描/研究期间健康 API | 不超预算且事件循环不阻塞 |

报告包含 CPU、内存、磁盘、Python/Node、代码、配置、数据、并发和缓存状态。单次最快结果不能代替 p95。

## 11. 最终闭环 E2E

必须在固定历史交易日完成：

1. PIT 数据同步和门禁；
2. 市场宽度；
3. 版本化 scan profile 扫描；
4. 六个插件各完成命中、不命中、未来数据拒绝和异常隔离 fixture；至少两种插件在固定真实历史中生成独立观察；
5. 信号进入 WATCHING 或 TRADEABLE，研究实验保持隔离；
6. 只有研究状态 ACTIVE_FOR_A_POOL 的插件可生成A池草稿；其余插件仅展示实验/观察；
7. 下一可交易日开盘正常或部分成交；
8. T+1、持仓、现金和风险快照；
9. 收盘日结和零差异对账；
10. outcome、归因、Review 和决策日志；
11. daily manifest、审计链和验证备份；
12. 重跑同一输入，排除ID/时间戳/审计envelope后的 canonical business result 与 SHA-256 完全一致。

再分别运行停牌、涨跌停、零量、公司行为、数据过期、对账差异、任务崩溃和备份损坏失败流程，全部 fail-closed。所有破坏性故障注入只能针对经过绝对路径校验的临时数据库副本/临时备份根，运行前后生产数据库 fingerprint 必须相同。

## 12. 发布检查命令

```powershell
.venv312\Scripts\python.exe -m pytest -q
.venv312\Scripts\python.exe -m ruff check . --exclude web/frontend/node_modules
.venv312\Scripts\python.exe -m mypy ab_screener paper_trading logic_platform web/backend_app.py
.venv312\Scripts\python.exe research_status.py --no-token-probe
npm --prefix web/frontend ci
npm --prefix web/frontend run test
npm --prefix web/frontend run build
npm --prefix web/frontend exec playwright install chromium
powershell -NoProfile -File scripts/run_browser_acceptance.ps1
.venv312\Scripts\python.exe -m pytest -q -m performance
.venv312\Scripts\python.exe -m pytest -q -m fault_injection
powershell -NoProfile -File scripts/restore_backup.ps1 -VerifyOnly
```

离线门禁必须覆盖仓库根目录与 `tests/` 下的全部可收集测试；不得用只跑 `tests/` 的命令遗漏根目录测试。真实 Token 与网络只用于独立发布门禁：

```powershell
.venv312\Scripts\python.exe -m paper_trading.real_data_gate --days 730 --report runtime/gates
```

还必须完成五个真实完成交易日的观察，不能在单次实现会话中伪造或压缩。命令退出 0 只证明执行成功；最终状态以结构化 gate JSON 为准。

## 13. 最终审计清单

- [ ] clean RC commit，工作区无未说明修改。
- [ ] 8001 为 AB、8000 为 AETF，构建身份无串台。
- [ ] 七份 gate JSON 均为 PASS，无 INSUFFICIENT。
- [ ] 总证据索引与各 artifact SHA-256 一致。
- [ ] PIT、ENTRY V1/V2、防未来函数和历史宇宙测试通过。
- [ ] PBO、DSR、MinTRL、Nested WF、基线、成本和容量证据齐全。
- [ ] 六形态 registry 完整；未验证插件只在实验区。
- [ ] 同一 fixture 研究/纸面成交逐笔一致、现金误差零分。
- [ ] 组合约束、风险手算和压力情景全部通过。
- [ ] 最新交易日 daily manifest COMPLETE、日结和对账无差异。
- [ ] DAG 幂等、崩溃续跑、Windows 竞态和重复启动测试通过。
- [ ] 最近备份验证成功，并完成一次实际恢复。
- [ ] 全站写操作审计覆盖 100%，篡改检测通过。
- [ ] UTF-8 报告无乱码，旧/过期证据不显示当前 PASS。
- [ ] 浏览器全部按钮、390px、键盘、焦点和状态恢复通过。
- [ ] Token 扫描零命中；明文供应商通道如仍存在则安全闸门不得全绿。
- [ ] `LIVE_TRADING_ENABLED=false`，仓库不存在真实券商下单实现。
- [ ] 回滚手册已实际演练并记录结果。
- [ ] 独立审计者签署最终结论与已知限制。

只有上述清单全部完成，且真实研究 R 闸门本身通过，系统才可标记 `PERSONAL_INSTITUTIONAL_READY`。只有 D/S/P/L/O/G 全部 PASS 且 R 单独未通过时，才标记 `ENGINEERING_READY_RESEARCH_BLOCKED`；其他任何硬门未通过时仍为 `BLOCKED`。
