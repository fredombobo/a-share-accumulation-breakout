# 龙虎榜机构／主力／游资追踪系统——实施任务清单与验收标准

> 文档状态：T01–T12 工程已交付并形成隔离副本研究产品（2026-08-29）；真实 5 日链路、浏览器与全量测试已通过；官方跨源核验未获授权，故第 6 节正式「工程通过」仍不宣告；研究门禁 `RESEARCH_BLOCKED`。详见 `docs/ACCEPTANCE-LHB-V1.md`。  
> 目标仓库：`E:\CODEX\Stock_selection\accumulation_breakout`  
> 系统定位：个人研究、盘后分析、模拟跟踪；不是实盘自动交易系统。  
> 最终验收人：主任务 Agent（所有分项 Agent 完成交接后统一复核）。

本计划在现有 `top_list`、PIT 历史表、持久化 DAG、告警表和 v2 API 骨架上扩展龙虎榜席位级能力。实施顺序为：先冻结数据契约和证据口径，再完成抓取、标准化、席位映射、画像与研究信号，最后接入仪表盘、调度、告警和样本外验证。任何工程完成状态都不得被描述为“可保证赚钱”或“可实盘跟单”。

## Scope

- In：沪深 A 股龙虎榜汇总与席位明细、席位别名和身份假设、机构／游资／行为型主力分类、画像、研究信号、回测、API、仪表盘、盘后调度和通知审计。
- Out：券商账户接入、自动下单、实盘仓位管理、L2 逐笔、破解验证码或反爬、未经授权的数据再分发、以营业部名称断言具体自然人身份。

## 0. 全局硬约束

- [x] `LIVE_TRADING_ENABLED` 始终为 `false`；本计划不得新增真实下单入口。
- [x] `V2_PIT_READ_ENABLED`、`DAILY_SCHEDULER_ENABLED` 在各自正式门禁完成前保持 `false`。
- [x] 所有真实迁移、回填和破坏性试验只针对明确的数据库副本；禁止直接操作 `runtime/stock_data.db`。
- [x] Tushare 只能通过仓库 `tushare_init.py` 获取 `pro`；禁止另建 Token、URL 初始化路径，禁止裸 `requests` 直连 Tushare。
- [x] 不修改已经发布的 migration intent；新增迁移必须使用新 migration id，避免 checksum 漂移。
- [x] 原始数据和标准事实均保留 `source`、`available_at`、`ingested_at`、`revision`、`content_hash`。
- [x] 当前游资名单只作为身份假设；无权威证据不得输出“确定为某自然人”。
- [x] “主力”仅表示行为分类或资金风格，不表示已识别最终受益人。
- [x] 不提交 `.env`、Token、生产数据库、原始大体积数据、个人通知凭据。
- [x] 每个任务完成后写 `docs/handoffs/LHB-TXX.md`，记录改动、测试、真实证据、未完成项和回滚方式；不得用口头结论替代证据。

## 1. 功能与总体验收矩阵

| ID | 功能 | 最低可交付能力 | 验收标准 | 依赖 |
|---|---|---|---|---|
| F01 | 数据源注册 | 上交所、深交所、Tushare 数据源有统一契约和状态 | 可区分 `VALID_EMPTY / NOT_PUBLISHED / FETCH_FAILED / DEGRADED / COMPLETE`；每次抓取有 manifest | T01 |
| F02 | 龙虎榜汇总抓取 | 按交易日增量抓取 `top_list` | 同一分区重跑幂等；失败可续跑；交易日补洞不是只看最大日期 | T02 |
| F03 | 席位明细抓取 | 按交易日抓取 `top_inst` 席位买卖明细 | 字段、金额单位、日期、行数通过契约测试；单源失败不伪装为空 | T02 |
| F04 | 官方源核验 | 上交所／深交所公开信息可作为最终核验源 | 与 Tushare 的股票、原因、期间、席位金额形成对账报告；差异不静默覆盖 | T02–T03 |
| F05 | PIT 历史与回填 | 新数据集 append-only、可恢复回填 | 迁移幂等；中断后从 checkpoint 续跑；覆盖率和 hash 报告可复算 | T01、T03 |
| F06 | 龙虎榜事件标准化 | 统一单日榜、累计榜、多个上榜原因 | 同一事件不会因多原因重复计入资金流；事件期间明确 | T04 |
| F07 | 席位买卖去重 | 分离席位金额与买／卖榜排名 | 同一席位同时进买榜和卖榜时金额只统计一次，排名均保留 | T04 |
| F08 | 席位标准化 | 原始名称映射到稳定 `seat_id` | 全半角、空格、券商后缀和历史更名有测试；原始名称不丢失 | T05 |
| F09 | 身份假设图谱 | 席位可映射到机构类别、游资候选或未知 | 多对多、带有效期、证据来源和置信度；冲突可见；不强制唯一归属 | T05 |
| F10 | 自动行为分类 | 识别打板、趋势、低吸、做 T、短持等概率 | 输出概率、样本量和模型版本；不把行为分类改写为实名身份 | T06 |
| F11 | 历史操作追踪 | 席位／身份／股票多维历史查询 | 可查询买卖额、净额、上榜次数、板块、市值、换手和未来收益 | T06–T07 |
| F12 | 席位画像 | 20/60/120/250 日滚动风格画像 | 无未来数据；小样本胜率经过收缩；展示置信区间和样本量 | T07 |
| F13 | 资金流与共振 | 生成归一化资金强度和独立席位共识 | 避免关联席位重复投票；净买额可追溯到明细事实 | T07 |
| F14 | 研究型跟随信号 | 产生 `WATCH / CONFIRMED_FLOW / RESEARCH_ENTRY / NO_CHASE / INVALIDATED` | 每个信号有分项得分、硬否决原因、数据版本和最早可执行时间 | T08 |
| F15 | 回测与结果回填 | 下一交易日开盘模型、可成交约束、未来收益 | 无未来函数；含费用、滑点、涨跌停、停牌、T+1和退市；结果可复现 | T09 |
| F16 | 每日雷达 API | 每日事件、席位、股票、映射、信号可查询 | OpenAPI 契约稳定；分页、日期、置信度过滤生效；无数据语义明确 | T10 |
| F17 | 仪表盘 | 每日雷达、席位画像、股票时间线、数据质量、回测页 | 空态、降级态、加载失败、低置信标签均可见；前端构建通过 | T10 |
| F18 | 调度和恢复 | 盘后 DAG、租约、重试、次日修订 | 重跑幂等；崩溃续跑；并发只允许一个 lease；失败阻断下游 | T11 |
| F19 | 异动告警 | 大额买入、共振、重复出现、买转卖、数据异常 | 告警幂等；有送达状态、重试和失败队列；未 ACK 不算送达 | T11 |
| F20 | 研究门禁与集成 | 龙虎榜只作为现有选股系统的 research overlay | 样本外和 shadow 未达标时不进入 A 池、不生成订单、不翻生产旗标 | T12 |

## 2. Action items

> 2026-08-29 返工复验：T01–T05 已通过，T06 工程依赖已放行。详见 `docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`。

### [x] T01 — 冻结数据契约、迁移和状态模型

**实现功能**

- 新增龙虎榜领域说明、字段字典、金额单位、时间语义和错误状态。
- 新增 migration intent，建议包含：
  - `lhb_event`
  - `lhb_seat_trade`
  - `lhb_seat_rank`
  - `seat_master`
  - `seat_alias`
  - `actor_master`
  - `seat_actor_hypothesis`
  - `lhb_reconciliation`
  - `lhb_feature_snapshot`
  - `lhb_signal_observation`
  - `lhb_signal_outcome`
- 新增 `top_inst_history`、游资名单原始历史表或等价的 append-only staging 表。
- 事件业务键至少覆盖交易所、证券、统计期间、上榜原因和披露日期。
- 金额事实与买／卖榜排名分表，避免同席位双榜重复金额。

**建议文件**

- `ab_screener/data/migration_intents/lhb_tracking_v2.py`
- `ab_screener/domain/lhb_contracts.py`
- `docs/DATA-DICTIONARY-LHB-V1.md`
- `tests/test_lhb_migrations.py`
- `tests/test_lhb_contracts.py`

**验收标准**

- [x] 在空数据库和已有 v2 schema 副本上各执行两次迁移，第二次无重复对象、无数据变化、无异常。
- [x] 已有 migration 文件 checksum 不变。
- [x] 历史／映射事实表禁止 UPDATE/DELETE，修订只能追加新 revision 或新增有效期记录。
- [x] 唯一键能表示同股同日多个原因、单日榜和累计榜。
- [x] `available_at` 使用带时区时间，统一为 `Asia/Shanghai`。
- [x] 所有金额字段明确统一为“元”；进入领域层前完成单位转换。
- [x] 单元测试覆盖非法日期、负金额、买卖净额不一致、未知状态和重复键。

**交接证据**：`docs/handoffs/LHB-T01.md`

---

### [x] T02 — 实现数据源适配、增量抓取和原始快照

**实现功能**

- 扩展 Tushare PIT 适配器，支持 `top_inst`，并可选支持 `hm_list` 初始化候选名录。
- 为上交所、深交所公开信息定义统一 adapter；若官方接口存在授权或反爬限制，必须 fail-closed 并记录原因，不得绕过限制。
- 保存原始响应摘要、内容 hash、请求时间、可用时间、来源状态和行数。
- 支持按交易日抓取、交易日历补洞、指数退避重试、限速和熔断。
- 主源失败时可使用备用源，但产物标记 `DEGRADED`，不得宣称完整。

**建议文件**

- `ab_screener/data/adapters/lhb_sources.py`
- `ab_screener/data/adapters/tushare_pit.py`
- `ab_screener/application/lhb_ingest.py`
- `tests/test_lhb_source_adapters.py`
- `tests/fixtures/lhb/`

**验收标准**

- [x] 所有在线调用均可通过 fake client 离线测试；CI 不依赖真实 Token 或网络。
- [x] 相同交易日、相同响应重跑不会新增重复事实；manifest hash 相同。
- [x] `VALID_EMPTY`、`NOT_PUBLISHED`、`FETCH_FAILED` 能由独立 fixture 复现。
- [x] 网络超时、限流、字段缺失、HTML结构变化不会写入“成功空数据”。
- [x] 重试次数有上限，错误保留最后原因，不无限循环。
- [x] Token 和完整原始响应不会出现在日志和测试快照中。
- [x] 真实接口 smoke test 只在显式传入 Token 时运行，并在 handoff 中记录日期、行数和响应字段，不记录 Token。

**交接证据**：`docs/handoffs/LHB-T02.md`

---

### [x] T03 — 完成历史回填、跨源对账和数据质量门禁

**实现功能**

- 扩展现有 PIT backfill，加入 `top_inst` 和龙虎榜分区覆盖率。
- 支持 checkpoint、失败分区重试、内容 hash 抽样核对和 T+1 修订。
- 生成交易所与 Tushare 的股票、上榜原因、统计期间、席位、买卖金额差异报告。
- 对金额单位漂移、重复率、未知席位率、字段缺失率设置质量阈值。

**建议文件**

- `ab_screener/application/pit_backfill.py`
- `scripts/backfill_pit_v2.py`
- `ab_screener/application/lhb_reconcile.py`
- `tests/test_lhb_backfill_resume.py`
- `tests/test_lhb_reconciliation.py`

**验收标准**

- [x] 所有回填先对明确的绝对路径数据库副本执行，命令遇到生产 DB 路径应拒绝或要求专用维护授权。
- [x] 人工中断后可从最后成功分区继续，不重复已成功分区。
- [x] 覆盖率按交易日历计算，不因周末或法定节假日误报缺日。
- [x] 对账差异逐条保留双方原始值、差异类型和处理状态，不静默覆盖。
- [x] 零行分区只有在来源明确返回已发布且无榜单时才能标为 `VALID_EMPTY`。
- [x] 随机抽样至少 20 个交易日与 20 个席位事件可从标准事实追溯到 raw manifest。
- [x] 未达到约定覆盖率和跨源一致率时，数据门禁返回 `INSUFFICIENT`，不允许后续信号标为 confirmed。

**交接证据**：`docs/handoffs/LHB-T03.md`

---

### [x] T04 — 实现事件标准化、期间解析和席位金额去重

**实现功能**

- 将上榜原因标准化为带版本的 `reason_code`，同时保存原始文本。
- 解析单日、连续三日、十日／三十日严重异常等统计期间。
- 将“席位金额事实”和“进入买榜／卖榜及排名”拆开。
- 生成事件内容指纹，识别不同原因下重复披露的同一组成交。
- 支持同一席位同时进入买榜和卖榜、同一股票多个原因、累计榜和单日榜并存。

**建议文件**

- `ab_screener/domain/lhb_normalization.py`
- `ab_screener/application/lhb_transform.py`
- `tests/test_lhb_normalization.py`
- `tests/fixtures/lhb/duplicate_cases.json`

**验收标准**

- [x] fixture 中同席位双榜的买卖额只计一次，两个 rank 均保留。
- [x] 同一成交内容对应多个上榜原因时，资金汇总只计一次，原因标签全部保留。
- [x] 累计三日数据不会被错误归入披露日的单日流量。
- [x] 无法解析期间时状态为 `UNRESOLVED_WINDOW`，不得猜测日期。
- [x] `sum(seat net)`、榜单公布合计和股票成交额的关系有可解释质量检查；不要求不可能成立的严格恒等式。
- [x] 标准化结果重跑确定性一致，输入顺序变化不改变 event fingerprint。

**交接证据**：`docs/handoffs/LHB-T04.md`

---

### [x] T05 — 建立席位主数据、别名历史和身份假设图谱

**实现功能**

- 实现名称 NFKC、空格、全半角、券商法律名称、城市、道路和营业部层级标准化。
- 维护券商更名、营业部迁址、分公司变化的 `valid_from/valid_to`。
- 实现官方标签规则：机构专用、沪股通专用、深股通专用、总部／非营业场所、普通营业部。
- 导入 `hm_list` 或人工维护名录作为低／中置信身份假设。
- 支持一席位多候选、一候选多席位、证据冲突和过期映射。

**建议文件**

- `ab_screener/domain/seat_identity.py`
- `ab_screener/data/seat_repository.py`
- `configs/lhb_identity_policy.yaml`
- `tests/test_seat_identity.py`
- `tests/fixtures/lhb/seat_aliases.csv`

**验收标准**

- [x] `机构专用`只能识别为机构通道，不能自动细分为具体公募、私募或 QFII。
- [x] `沪股通专用/深股通专用`标记为互联互通聚合通道，不标记为单一外资机构。
- [x] 第三方游资映射默认不是 A 级证据，输出文字带“疑似／候选”。
- [x] 任何 identity 查询都按事件日期读取当时有效版本，不使用今天的映射回填历史。
- [x] 两个标准名称冲突时不自动合并，进入人工复核队列。
- [x] 标准化 precision 抽样复核达到预设阈值；不得只汇报覆盖率而不汇报误合并率。
- [x] **T10 跨任务依赖（不阻断 T05 领域验收）**：API／UI 展示证据来源、置信度、有效期和冲突状态。

**交接证据**：`docs/handoffs/LHB-T05.md`

---

### [x] T06 — 实现行为特征、风格分类和协同网络

**实现功能**

- 生成席位在 20/60/120/250 日窗口内的规模、方向、频率、纯度和持续性特征。
- 生成行业、市值、换手率、首板／连板、趋势／反转偏好。
- 计算与其他席位的共现网络，并按 actor 关联关系去除非独立重复投票。
- 输出打板型、趋势波段型、低吸反包型、做 T 型、撬板型等行为概率。
- 加入时间衰减、样本量门槛和行为漂移检测。

**建议文件**

- `ab_screener/features/lhb_features.py`
- `ab_screener/research/seat_style.py`
- `ab_screener/research/seat_network.py`
- `tests/test_lhb_features.py`
- `tests/test_seat_style.py`

**验收标准**

- [x] 所有特征仅使用 `available_at <= as_of` 的事实。
- [x] 输入不足时返回 `INSUFFICIENT_SAMPLE`，不以 0 填充伪装有效。
- [x] 风格输出为概率分布，概率和在允许误差内为 1，并记录模型版本。
- [x] 关联席位被识别为同一 actor 时，共振计数只算一个独立主体。
- [x] 特征对输入行顺序、重复抓取和 revision 选择具有确定性。
- [x] 漂移检测有合成数据测试，能够识别明显风格改变，也不会对小样本报警。

**交接证据**：`docs/handoffs/LHB-T06.md`

---

### [x] T07 — 生成席位、身份、股票和板块画像

**实现功能**

- 聚合买入额、卖出额、净额、上榜次数、行业偏好、市值偏好和换手特征。
- 计算下一开盘入场后的 1/3/5/10/20 日原始收益与基准超额。
- 对胜率、平均收益和风格比例使用小样本收缩及置信区间。
- 生成每日不可变画像快照，允许重现历史看板。

**建议文件**

- `ab_screener/application/lhb_profiles.py`
- `ab_screener/data/lhb_repository.py`
- `tests/test_lhb_profiles.py`

**验收标准**

- [x] 样本为 3 次且全部盈利时，不展示未经收缩的“100%可靠胜率”。
- [x] 画像显示样本量、统计窗口、最近数据日期和置信区间。
- [x] 未来收益使用真实下一交易日和复权／公司行为口径，停牌与未成交状态单独记录。
- [x] 席位画像、actor 画像和股票画像不得混用统计口径。
- [x] 任意画像指标可下钻到构成事件，金额总和可复算。

**交接证据**：`docs/handoffs/LHB-T07.md`

---

### [x] T08 — 实现研究型资金流评分、信号和硬否决

**实现功能**

- 计算净买额／当日成交额、净买额／ADV20、方向纯度、独立共振、历史 edge、市场环境和拥挤度。
- 生成 `WATCH`、`CONFIRMED_FLOW`、`RESEARCH_ENTRY`、`NO_CHASE`、`INVALIDATED` 状态。
- 为每个信号保存各分项得分、阈值版本、数据版本、身份版本、最早可执行时间和否决原因。
- 增加数据不完整、身份低置信、无法成交、流动性不足、高位拥挤、严重异常波动等硬否决。

**建议文件**

- `ab_screener/domain/lhb_signal.py`
- `ab_screener/application/lhb_signal_engine.py`
- `configs/lhb_signal_policy.yaml`
- `tests/test_lhb_signal_engine.py`

**验收标准**

- [x] 信号永远在披露后产生，最早执行时间不得早于下一交易日。
- [x] 原始金额相同但成交额不同的股票，其资金强度不同且符合归一化定义。
- [x] 数据门禁不完整时最多输出 `WATCH`，不能输出 `CONFIRMED_FLOW/RESEARCH_ENTRY`。
- [x] 次日一字涨停、停牌、流动性不足的例子触发 `NO_CHASE` 或不可成交，不生成虚假买入。
- [x] 每个分数和否决结论可以从保存的 feature snapshot 重算。
- [x] 配置阈值修改产生新 policy version，历史信号不被悄然重写。

**交接证据**：`docs/handoffs/LHB-T08.md`

---

### [x] T09 — 建立点时正确的事件研究、回测和反过拟合门禁

**实现功能**

- 使用披露后下一交易日开盘作为默认入场模型。
- 加入佣金、印花税、滑点、冲击成本、涨跌停、停牌、T+1、退市和公司行为。
- 建立匹配对照组：同上榜原因、日期、市值、涨跌幅、换手率、连板高度、板块和市场环境。
- 做 1/3/5/10/20 日事件研究、滚动 walk-forward、锁定 holdout、参数试验登记和 PBO／DSR 等诊断。
- 将预测时点、成交时点和结果成熟时点分别记录。

**建议文件**

- `ab_screener/research/lhb_event_study.py`
- `ab_screener/research/lhb_backtest.py`
- `ab_screener/research/lhb_validation.py`
- `tests/test_lhb_backtest_no_lookahead.py`
- `tests/test_lhb_event_study.py`

**验收标准**

- [x] 专门的反未来函数测试证明：修改信号日之后才发布的数据不会改变历史信号。
- [x] 当前游资名单不会倒灌到映射生效日前的事件。
- [x] 无法买入的涨停样本不按开盘价成交；无法卖出的跌停按执行模型延期。
- [x] 回测报告同时给出毛收益、净收益、基准超额、最大回撤、容量、样本量和置信区间。
- [x] 匹配对照和不匹配原始收益同时展示，避免把龙虎榜选择偏差当成席位 alpha。
- [x] 所有参数试验进入 trial registry；不得只保留表现最好的结果。
- [x] 未通过样本外、反过拟合或容量门禁时，结论必须是 `FAIL` 或 `INSUFFICIENT_EVIDENCE`。

**交接证据**：`docs/handoffs/LHB-T09.md`

---

### [x] T10 — 提供 API、每日雷达和可审计仪表盘

**实现功能**

- 新增 API：每日事件、席位详情、股票时间线、身份映射、数据质量、研究信号和回测摘要。
- 新增前端页面：
  - 每日龙虎榜雷达
  - 席位／actor 画像
  - 股票龙虎榜时间线
  - 协同席位网络
  - 数据质量与缺口
  - 回测与 shadow 状态
- 对低置信身份、降级数据、未发布、抓取失败和样本不足提供明确视觉状态。

**建议文件**

- `ab_screener/api/routers/lhb.py`
- `web/frontend/src/api/lhb.ts`
- `web/frontend/src/types/lhb.ts`
- `web/frontend/src/pages/v2/LhbRadar.tsx`
- `tests/test_lhb_api_contract.py`

**验收标准**

- [x] API 支持日期、证券、席位、actor 类型、置信度、信号状态和分页过滤。
- [x] API 返回 `as_of`、`available_at`、source status 和 policy/model version。
- [x] 同一接口对空数据、未发布和失败返回不同状态，不统一返回空数组掩盖问题。
- [x] 前端金额单位和后端一致，不发生万元／元二次换算错误。
- [x] 身份标签旁展示置信度和证据级别；低置信不得使用确定语气。
- [x] `npm ci` 与 `npm run build` 通过；OpenAPI 契约测试通过。
- [x] 不手工编辑 `web/frontend/dist` 的 hash 文件；只由正式构建生成。

**交接证据**：`docs/handoffs/LHB-T10.md`

---

### [x] T11 — 接入盘后 DAG、告警状态机和通知确认

**实现功能**

- 在现有持久化 DAG 中加入龙虎榜抓取、核验、标准化、映射、特征、信号和报告步骤。
- 支持交易日 lease、最多三次 attempt、崩溃续跑、T+1修订和失败阻断。
- 新增大额净买入、多独立席位共振、连续出现、买转卖、映射漂移和数据质量告警。
- 扩展告警状态为 `CREATED / SENT / ACKED / FAILED / DEAD_LETTER` 或等价可审计模型。
- 通知复用现有企微、Discord、Hermes 通道健康检查，不新增明文凭据。

**建议文件**

- `ab_screener/operations/dag.py`
- `ab_screener/operations/scheduler.py`
- `ab_screener/operations/alerts.py`
- `ab_screener/application/lhb_daily.py`
- `tests/test_lhb_daily_dag.py`
- `tests/test_lhb_alerts.py`

**验收标准**

- [x] 相同 `trade_date + step + scope + input_hash` 最多成功一次，重跑不重复抓取事实或发送告警。
- [x] 两个调度实例竞争同一交易日时只有一个获得有效 lease。
- [x] 上游 `FETCH_FAILED/DEGRADED` 按政策阻断 confirmed signal，但仍可生成数据质量告警。
- [x] 告警 `dedupe_key` 防止同一规则重复推送；失败后按上限重试并进入 dead letter。
- [x] 通知调用成功但无 ACK 时不显示为“已送达”。
- [x] 手工历史重放不发送真实通知，除非显式使用测试 channel。
- [x] 至少完成 5 个连续交易日的副本 soak；`dag_runs`、step attempts、lease、audit、alert 状态可核验。
- [x] soak 前不得打开 `DAILY_SCHEDULER_ENABLED`。

**交接证据**：`docs/handoffs/LHB-T11.md`

---

### [x] T12 — Shadow 验证、现有系统集成和最终发布门禁

**实现功能**

- 将龙虎榜信号以 `research_only` overlay 接入现有候选解释层，不直接修改 A 池交易逻辑。
- 建立 shadow observation/outcome 生命周期，持续记录真实披露、下一开盘可成交和成熟结果。
- 输出独立的工程状态与研究状态：工程 PASS 不等于 edge PASS。
- 建立 feature flag、回滚和最终验收报告。

**建议文件**

- `ab_screener/application/lhb_overlay.py`
- `ab_screener/application/readiness.py` 或现有 readiness 扩展点
- `docs/ACCEPTANCE-LHB-V1.md`
- `tests/test_lhb_overlay_boundaries.py`
- `tests/test_lhb_readiness.py`

**验收标准**

- [x] 龙虎榜 overlay 关闭时，现有 A/B 池结果和公开 API 不发生非预期变化。
- [x] overlay 打开但研究门禁未过时，只增加解释字段，不改变仓位、不生成订单。
- [x] shadow 记录包含信号日期、披露时间、最早执行时间、可成交性、策略版本和结果成熟状态。
- [ ] 至少经过 3 个月且有不少于 30 个成熟独立信号才能称为最低 shadow maturity；建议达到 6–12 个月／100个独立事件后再讨论策略晋升。
- [x] 样本外净超额、回撤、容量、稳定性或反过拟合任一失败，readiness 返回 `RESEARCH_BLOCKED`。
- [x] 最终报告明确列出已通过项、未通过项、已知限制和原始证据路径；禁止用总分掩盖硬门失败。
- [x] 未经单独验收，不打开 `V2_PIT_READ_ENABLED`、`DAILY_SCHEDULER_ENABLED`，永不打开 `LIVE_TRADING_ENABLED`。

**交接证据**：`docs/handoffs/LHB-T12.md`

## 3. Agent 分派建议

以下任务可以由不同 Agent 实施，但有依赖关系，不应并发修改同一核心文件：

| 波次 | 可并行任务 | 前置条件 | 主要冲突文件 |
|---|---|---|---|
| Wave A | T01 | 无 | migration registry、领域契约 |
| Wave B | T02、T04 | T01完成 | `tushare_pit.py`、新领域模块 |
| Wave C | T03、T05 | T02/T04完成 | repository、backfill、identity config |
| Wave D | T06、T07 | T04/T05完成 | feature/research 模块 |
| Wave E | T08、T09 | T06/T07完成 | signal、research、outcome |
| Wave F | T10、T11 | T08完成 | routers、DAG、alerts、frontend |
| Wave G | T12、最终独立验收 | T01–T11完成 | readiness、acceptance docs |

每个分项 Agent 的交付消息必须包含：

- 实际修改文件；
- 未修改但相关的现有脏文件；
- 定向测试命令与完整输出摘要；
- 是否使用真实网络／Token／数据库副本；
- 证据文件路径；
- 剩余风险；
- 是否触碰 feature flag；
- 回滚方法。

## 4. 边界情况验收清单

- [ ] 非交易日、节假日、半日交易或交易日历缺失。
- [ ] 当日确实无龙虎榜、尚未发布、接口失败三种状态。
- [ ] 上交所、深交所不同板块及规则版本变化。
- [ ] ST／*ST、主板、创业板、科创板上榜原因阈值不同。
- [ ] 单日榜、连续三日榜、十日／三十日严重异常榜并存。
- [ ] 同股同日多个上榜原因。
- [ ] 同一席位同时进入买入和卖出前五。
- [ ] 相同席位名称对应不同分支，或不同名称对应同一历史席位。
- [ ] 券商合并、营业部迁址、分公司改名、别名有效期重叠。
- [ ] `机构专用1/2/3` 等泛化标签，无法识别具体机构。
- [ ] 沪股通／深股通聚合席位被错误识别为单一外资。
- [ ] 游资第三方名录冲突或过期。
- [ ] 上游金额单位变化、精度变化、字段增删、行数截断。
- [ ] 同一累计事件因多来源或多原因重复入库。
- [ ] 当日涨停无法跟买、跌停无法卖、停牌、退市、除权除息。
- [ ] 次日大幅跳空导致研究信号存在但不宜追价。
- [ ] 小样本席位出现虚假100%胜率。
- [ ] 当前身份映射或模型版本被错误用于历史回测。
- [ ] 通知接口返回成功但用户侧没有 ACK。
- [ ] 调度进程崩溃、机器重启、并发双跑、checkpoint损坏。

## 5. 最终统一验收命令

所有命令均从 `E:\CODEX\Stock_selection\accumulation_breakout` 执行。最终验收 Agent 应保存完整日志和退出码，不能只摘录成功行。

```powershell
python scripts/check_architecture.py --strict
ruff check . --exclude web/frontend/node_modules
mypy signals.py optimizer.py walkforward.py local_store.py config.py `
  ab_screener/domain/costs.py ab_screener/domain/entry_definition.py `
  ab_screener/domain/entry_definition_v2.py `
  ab_screener/research/backtest_engine.py ab_screener/research/trusted_run.py `
  paper_trading/rules.py paper_trading/engine.py backtest_custom.py
pytest tests/ -q -k "not browser"
Push-Location web/frontend
npm ci
npm run build
Pop-Location
```

新增龙虎榜定向测试还必须全部通过（PowerShell 不会展开 `*`，不要写 `test_lhb_*.py`）：

```powershell
.\.venv312\Scripts\python.exe -m pytest `
  tests/test_lhb_contracts.py `
  tests/test_lhb_migrations.py `
  tests/test_lhb_source_adapters.py `
  tests/test_lhb_normalization.py `
  tests/test_lhb_reconciliation.py `
  tests/test_lhb_backfill_resume.py `
  tests/test_lhb_real_shape.py `
  tests/test_seat_identity.py `
  tests/test_lhb_features.py `
  tests/test_seat_style.py `
  tests/test_lhb_profiles.py `
  tests/test_lhb_signal_engine.py `
  tests/test_lhb_event_study.py `
  tests/test_lhb_backtest_no_lookahead.py `
  tests/test_lhb_api_contract.py `
  tests/test_lhb_daily_dag.py `
  tests/test_lhb_alerts.py `
  tests/test_lhb_overlay_boundaries.py `
  tests/test_lhb_readiness.py `
  tests/test_lhb_product_pipeline.py `
  tests/test_openapi_contract_v2.py `
  -q
```

或：`.\scripts\run_lhb_pytest.ps1`

## 6. 最终通过定义

只有同时满足以下条件，主验收人才可在 `docs/ACCEPTANCE-LHB-V1.md` 写“工程通过”：

- [x] T01–T12 全部有 handoff，并逐项核验证据而非只看自报结论。
- [ ] 数据、事件、身份、特征、信号、回测、API、前端、调度和告警验收全部通过。**阻断：真实官方跨源数据未获授权。**
- [ ] 全量离线 pytest、Ruff、架构检查、关键 mypy 和前端 build 通过。**pytest 806 passed、产品路径 Ruff、架构、关键 mypy、前端 build 已过；全仓 Ruff 仍有 114 条存量/范围外问题。**
- [ ] 数据库副本迁移、回填中断恢复、跨源对账和 5 日 DAG soak 有真实证据。**副本/恢复/真实 5 日 soak 已有；真实跨源对账仍为 `NOT_AUTHORIZED`。**
- [x] 生产数据库未被测试写入，所有生产 feature flag 保持安全状态。
- [x] 已证明不存在买／卖双榜金额翻倍、累计榜重复计算、身份映射未来函数和不可成交虚拟买入。
- [x] 通知具有可核验 ACK 或明确标记未送达。当前 68 条均为 `CREATED + dry_run=1`，明确未送达、未 ACK。

只有进一步满足样本外、反过拟合、容量和 shadow maturity 门槛，才可以把研究状态从 `RESEARCH_BLOCKED` 提升；即使研究状态通过，也不等于允许自动下单。

## 7. Open questions

- 是否购买或已具备足够的 Tushare `top_inst`、`hm_list` 调用权限；无权限时系统应保持 `INSUFFICIENT`，不能抓网页伪造替代数据。
- 官方交易所自动核验采用获准接口、合法页面抓取还是人工抽样核验；实施前需要记录数据使用边界。
- 通知首选企微、Discord 还是 Hermes；无论选择哪个，都必须实现送达状态和 ACK，不得仅以调用成功作为完成。
