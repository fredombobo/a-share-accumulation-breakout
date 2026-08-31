# LHB T01–T05 独立验收记录（2026-08-29）

> 最终结论：**T01–T05 返工复验通过，T06 工程依赖放行。**
>
> 本结论只评价 T01–T05 的工程实现，不评价龙虎榜策略是否具有可交易 alpha；系统继续保持 `research_only`，且不得据此自动下单。

## 1. 验收范围与环境

- 工作区：`E:\CODEX\Stock_selection\accumulation_breakout`
- 验收日期：2026-08-29（Asia/Shanghai）
- 真实只读抽样日：2026-08-03
- 真实接口：仅通过根 `tushare_init`；未输出 Token，未写生产数据库
- 生产库只读检查：`runtime/stock_data.db` 存在，未发现 LHB 新表
- 安全开关：
  - `V2_PIT_READ_ENABLED: false`
  - `DAILY_SCHEDULER_ENABLED: false`
  - `LIVE_TRADING_ENABLED: false`

## 2. 测试结果

| 检查 | 结果 |
|---|---:|
| `scripts/run_lhb_pytest.ps1` | 94 passed |
| PIT／迁移相邻回归 | 34 passed |
| LHB 相关 Ruff | PASS |
| LHB 相关 mypy | PASS（9 files） |
| `scripts/check_architecture.py --strict` | PASS |
| 全量 `pytest tests/ -q -k "not browser"` | collection FAIL：既有 `v2:corporate_action_pit` 注册问题，4 errors |
| Tushare smoke | top_list 56、top_inst 580、hm_list 113 |

专项测试全绿不等于验收通过。真实抽样和反例测试发现以下硬阻断。

## 3. 初验分任务判定（历史记录，已由第 7 节取代）

### T01 — 不通过

P0：数据字典把 Tushare `top_list/top_inst` 来源金额写成“万元”。Tushare 官方 `top_inst` 文档明确 `buy/sell/net_buy` 为“元”，真实返回数值也与元口径一致。该错误会使领域金额口径失真，违反“进入领域层前完成正确单位转换”的验收项。

返工要求：

- 分数据集、分字段冻结来源单位，禁止把 `top_list`、`top_inst` 和其他资金流接口共用一个未经证明的默认单位。
- 增加真实量级回归：原始 `1,013,162,595.79` 元进入领域层后仍为同额人民币，不得变成 `10,131,625,957,900.00` 元。
- 修正数据字典、handoff 和所有单位示例。

### T02 — 不通过

P1：缺少 `buy/sell/net_buy` 等金额字段的 `top_inst` 行仍会被标记为 `COMPLETE`；当前只校验业务键，不满足“字段缺失不能写成功数据”。

P1：默认重试 sleeper 是空函数，生产默认路径没有实际指数退避；只有测试注入 callback 时才记录等待值。

返工要求：

- 为 `top_list/top_inst/hm_list` 定义版本化必需字段及数值有限性校验；缺失、NaN 位于本方向关键金额、非法负数时 fail-closed。
- 默认使用真实、可注入的 sleep；测试继续注入零等待实现。
- 增加缺金额字段、NaN、无穷值和默认退避测试。

### T03 — 不通过

P0：已标记 `done` 的回填分区在下一次运行时直接跳过且不再请求上游，无法发现或追加 T+1 修订，与任务定义相冲突。反例中上游金额从 1 改为 2，第二次运行 `skipped=1`，网关总调用次数仍为 1。

P1：对账 `recon_id` 包含左右金额值。金额变化后产生新的 `recon_id/revision=1`，不是同一业务差异的 revision 2。与此同时，持久化载荷没有席位、原因和 side 标识，同股同字段同金额的多个席位差异可能折叠。

P1：`reconcile_sources` 声称单位必须显式提供，但函数仍默认双方为 `wan_yuan`；真实 Tushare 为元。

返工要求：

- 增加明确的修订重查策略（例如最近 N 个交易日强制重验），内容 hash 未变才跳过，变化追加 revision。
- 用稳定业务键生成 `recon_id`，金额值只进入 `content_hash`；保存 `exchange/seat/reason/window/side` 等定位字段。
- 移除危险单位默认值，调用方必须显式声明并记录来源字段单位。
- 新增“内容变化 revision+1”“两个席位同金额差异不折叠”“T+1 修订可发现”测试。

### T04 — 不通过

P0：`transform_day` 默认把 Tushare 席位金额当作万元。真实抽样最大买额按默认路径从 `1,013,162,595.79` 元变成 `10,131,625,957,900.00` 元，正好放大 10,000 倍。

P0：原因规则不覆盖真实数字写法和否定词。2026-08-03 抽样至少发现：

- `连续3个交易日...` 被归为 D1，而不是 D3；
- 北交所 `连续3个交易日...涨跌幅偏离...` 被误归为单日跌幅；
- `日收盘价格涨幅达到15%` 被归为 `UNKNOWN`；
- `非上市首日...可转债` 因包含“上市首日”被误归为 `IPO_FIRST_DAY`。

这些误分类会进一步把单日与累计席位明细混到同一资金事实中。

返工要求：

- Tushare `top_inst` 走明确 `yuan` 口径；质量核对中的 `top_list` 字段单位也必须逐字段确认。
- 原因解析先处理否定词、数字／中文数字、`连续3个交易日`、北交所及 20% 涨跌幅板块文本；必要时先过滤可转债等非目标证券。
- 用真实 2026-08-03 原因集合建立冻结 fixture，逐条断言 reason/window，并验证 D1/D3 席位集合不交叉。
- 保留现有已通过的双榜去重与输入顺序确定性测试。

### T05 — 不通过

P0：游资 actor 的主键由席位标准名生成，而不是候选游资实体生成。因此“同一游资对应多个席位”会创建多个 actor；“同一席位对应多个候选”会因同一主键被静默跳过，未实现任务要求的多对多图谱。

P0：`lookup_as_of` 只按 `valid_from/valid_to` 过滤，没有按当时的 `available_at` 过滤，也没有 `knowledge_as_of` 参数。反例中 2026 年才入库、但 `valid_from=19900101` 的映射，在查询 2020 年事件时可见，形成身份未来函数。

P1：precision 测试只是对手工输入 `100/80/2` 计算公式，没有真实／人工标注抽样，因此不能证明 coverage 和误合并率达到政策阈值。

未完成：任务清单中的 API／UI 证据展示仍为未勾选；如约定归 T10，应在 T05 验收标准中明确拆分为跨任务依赖，不能同时把 T05 标成全部完成。

返工要求：

- actor_id 基于稳定候选实体 ID；同一席位允许多个候选，同一候选允许多个席位，均带独立证据、有效期、revision 和冲突状态。
- 查询同时接受 `event_date` 与 `knowledge_as_of`，所有 master/alias/hypothesis 版本都满足 `available_at <= knowledge_as_of`。
- 对真实 `hm_list.orgs` 和人工名录做可复核抽样，保存样本、标注、coverage、false merge 和阈值判定。
- 增加多对多、未来 available_at、证据修订、有效期重叠冲突测试。

## 4. 已确认通过且应保留的部分

- migration 可在临时库重复执行，append-only 约束和现有 checksum 测试通过。
- `side='0'/'1'` 已映射到 BUY/SELL。
- 真实抽样 580 行中识别到 95 个同席位同原因多行组、88 个双榜组；标准化后未出现重复 `(event_id, seat)`。
- D1/D3 合成 fixture 的席位隔离、双榜金额不相加、manifest 双链追溯和相同对账内容重跑幂等测试通过。
- 生产数据库未迁移，三项安全开关均保持关闭。

## 5. T06 放行条件

T06 依赖 T04/T05 的金额、期间和 actor 身份。以下条件未全部满足前，**不得把 T06 标为完成，也不得使用当前事实生成风格或协同网络**：

1. 上述 P0/P1 全部修复并新增反例测试；
2. `scripts/run_lhb_pytest.ps1`、PIT 相邻回归、Ruff、mypy、架构检查继续通过；
3. 真实只读 smoke 的金额量级、原因窗口和多对多身份抽样通过；
4. 生产库无写入，三项 feature flag 保持 false；
5. handoff 中逐项回应本验收记录，不得只写“测试通过”。

返工复验已满足上述五项，T06 工程实现现已放行；该放行不改变研究门禁与禁实盘约束。

## 6. 当前研究状态

- 工程状态：`T01_T05_ACCEPTED`
- T06 gate：`ENGINEERING_DEPENDENCIES_RELEASED`
- 研究状态：`RESEARCH_ONLY / INSUFFICIENT_EVIDENCE`
- 自动交易：禁止

## 7. 返工复验结果（最终判定）

| 任务 | 判定 | 关键关闭证据 |
|---|---|---|
| T01 | 通过 | `top_list/top_inst` 金额字段逐项冻结为元；`1,013,162,595.79` 元量级回归未被放大；字典已修正 |
| T02 | 通过 | v1 必需字段、NaN/Infinity/负金额均 fail-closed；默认真实指数退避；响应换序 hash 不变 |
| T03 | 通过 | 最近 5 个龙虎榜分区强制重验；内容变化追加 revision；稳定 `recon_id` 保留席位/原因/side locator；单位参数必填 |
| T04 | 通过 | 默认元口径；真实数字三日、北交所对称偏离、15% 价格涨跌原因均正确；非 A 股在事件创建前过滤 |
| T05 | 通过 | candidate actor 稳定 ID；一席位多候选/一候选多席位；master/alias/hypothesis/actor 均受 `available_at <= knowledge_as_of` 限制；人工标注 precision 门禁通过 |

复验命令与结果：

- `scripts/run_lhb_pytest.ps1`：94 passed。
- PIT／迁移相邻回归：34 passed。
- LHB 相关 Ruff：PASS。
- LHB 相关 mypy：10 files，PASS。
- `scripts/check_architecture.py --strict`：PASS。
- 全量 `pytest tests -q -k "not browser"`：仍在 collection 阶段被既有 `KeyError: v2:corporate_action_pit` 阻断，4 errors；与本次 T01–T05 改动无关。
- 生产库只读检查：没有 LHB 新表；`V2_PIT_READ_ENABLED`、`DAILY_SCHEDULER_ENABLED`、`LIVE_TRADING_ENABLED` 均为 `false`。

T05 的 API／UI 展示项按原任务分解归 T10，已在清单中明确为跨任务依赖，不阻断 T05 领域层验收。此次放行仅表示 T06 可以继续工程实现，不表示策略具有确定性 alpha、可以实盘跟单或能够保证收益。
