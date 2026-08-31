# DATA-DICTIONARY-LHB-V1 — 龙虎榜席位级数据字典

> 契约版本：lhb-v1 · 2026-08-29 · 对应迁移 `v2:lhb_tracking`  
> 系统定位：个人研究、盘后分析、模拟跟踪。不是实盘自动交易系统。  
> 权威代码：`ab_screener/domain/lhb_contracts.py`

## 0. 硬边界

- 龙虎榜产物默认 `research_only=1`；表约束禁止写成交易指令源。
- `LIVE_TRADING_ENABLED` 必须保持 `false`。本字典不定义券商下单字段。
- “主力”仅表示行为分类或资金风格，不表示已识别最终受益人。
- 游资名录只是身份假设；无权威证据不得输出“确定为某自然人”。
- `机构专用` 只能识别为机构通道，不得自动细分为公募 / 私募 / QFII。
- `沪股通专用` / `深股通专用` 是互联互通聚合通道，不是单一外资机构。

## 1. 单位与时间

| 项目 | 口径 |
|------|------|
| 货币 | CNY |
| 领域金额单位 | **元** |
| 存储 | 整数 **分**（`*_amount_fen`，1 元 = 100 分） |
| Tushare `top_list` 金额字段 | 元；按字段白名单声明，进入领域层后存整数分 |
| Tushare `top_inst.buy/sell/net_buy` | 元；禁止按万元二次放大 |
| 官方源单位 | 必须显式声明；未声明则拒绝写入 |
| 时间 | `Asia/Shanghai`，ISO-8601 带 `+08:00` |
| 交易日 / 披露日 | `YYYYMMDD` |
| `available_at` | 该版本真实可用时刻 |
| `ingested_at` | 本系统入库时刻 |
| `revision` | 同一业务键第几次修订，从 1 起；禁止 UPDATE/DELETE |

来源金额无法精确到分 → 拒绝。买/卖额不得为负；`net = buy - sell`，否则拒绝。

## 2. 抓取状态（manifest `source_status`）

| 状态 | 含义 | 行数约束 |
|------|------|----------|
| `COMPLETE` | 主源完整返回 | 必须 `row_count > 0` |
| `VALID_EMPTY` | 已发布且当日确实无榜 | 必须 `row_count = 0` |
| `NOT_PUBLISHED` | 该分区尚未发布 | 必须 `row_count = 0`，不是成功空数据 |
| `FETCH_FAILED` | 超时、限流、结构变化、授权失败 | 必须 `row_count = 0`，保留 `error_reason` |
| `DEGRADED` | 主源失败、备用源部分可用 | 不得宣称完整；不能把后续信号标为 confirmed |

零行分区只有在来源明确“已发布且无榜”时才能标 `VALID_EMPTY`。

## 3. 事件业务键

`lhb_event` 业务键（同 revision 唯一）：

`exchange + ts_code + window_code + reason_code + disclose_date`

因此允许：

- 同股同日多个上榜原因
- 单日榜（D1）与累计榜（D3 / D10 / D30）并存

`event_id` = 上述五元组的 SHA-256 前 32 位（UTF-8，`|` 连接）。

| `window_code` | 含义 |
|---------------|------|
| `D1` | 单日榜；`period_start = period_end = disclose_date` |
| `D3` | 连续三个交易日 |
| `D10` / `D30` | 严重异常波动统计窗 |
| `UNRESOLVED_WINDOW` | 无法解析期间；`period_start/end` 必须为空，禁止猜日期 |

原因目录版本：`reason_catalog_version=v1`。标准化代码见 `REASON_CODES_V1`；原始文本保存在 `reason_raw`。

## 4. 金额事实与排名分表

| 表 | 一行表示 | 主键 |
|----|----------|------|
| `lhb_seat_trade` | 某一事件下某一席位的买卖金额（只计一次） | `event_id, seat_raw, revision` |
| `lhb_seat_rank` | 该席位进入买榜或卖榜的名次 | `event_id, seat_raw, side, revision` |

同一席位同时进买榜和卖榜：金额一行，排名两行。

## 5. 表清单

### 5.1 PIT 原始历史（与现有 PIT 同构，append-only）

| 表 | 业务键 | 说明 |
|----|--------|------|
| `top_inst_history` | ts_code, trade_date, exalter, reason, side | Tushare 席位明细 |
| `hm_list_history` | hm_name, list_date | 游资名录快照（假设来源） |
| `lhb_official_raw_history` | exchange, trade_date, dataset | 上交所/深交所核验摘要；不存 Token，不把完整原始响应写入日志 |

已有 `top_list_history` 仍由 `v2:aux_history` 管理，本迁移不改其 DDL。

### 5.2 抓取清单

`lhb_ingest_manifests`：每次抓取一条（可修订）。字段含 `source_status`、`row_count`、`content_sha256`、`error_reason`、`available_at`、`ingested_at`。

### 5.3 标准事实与主数据

| 表 | 说明 |
|----|------|
| `lhb_event` | 标准化上榜事件 |
| `lhb_seat_trade` | 席位金额 |
| `lhb_seat_rank` | 买/卖榜排名 |
| `seat_master` | 稳定 `seat_id` + 官方标签 + 有效期 |
| `seat_alias` | 原始名称 / 历史别名 |
| `actor_master` | 机构通道 / 互联互通 / 游资候选 / 行为型主力 |
| `seat_actor_hypothesis` | 多对多、带有效期、证据来源、置信度、冲突状态 |
| `lhb_reconciliation` | 跨源差异双方原值，禁止静默覆盖 |
| `lhb_feature_snapshot` | 20/60/120/250 日不可变画像快照 |
| `lhb_signal_observation` | 研究信号；`research_only` 恒为 1 |
| `lhb_signal_outcome` | 1/3/5/10/20 日结果，修订追加 |

历史 / 映射事实表全部禁止 UPDATE/DELETE。修订只能插入新 `revision` 或新的 `valid_from` 记录。身份查询必须按事件日期读取当时有效版本。

## 6. 官方标签与身份假设

| `official_tag` | 含义 |
|----------------|------|
| `INSTITUTION_CHANNEL` | 机构专用席位（通道，非具体产品） |
| `SH_CONNECT` / `SZ_CONNECT` | 沪/深股通聚合 |
| `HQ_NON_BRANCH` | 总部 / 非营业场所 |
| `BRANCH` | 普通营业部 |
| `UNKNOWN` | 未分类 |

| `actor_type` | 展示口径 |
|--------------|----------|
| `INSTITUTION_CHANNEL` | 「机构专用通道」 |
| `CONNECT_CHANNEL` | 「沪深股通聚合通道」 |
| `HOT_MONEY_CANDIDATE` | 「疑似{名}（候选）」；第三方名录最高证据级 B |
| `BEHAVIORAL_MAIN_FORCE` | 「行为型主力（风格，非实名）」 |
| `UNKNOWN` | 「未知席位」 |

冲突不自动合并，`conflict_status=OPEN`。

## 7. 研究信号状态（表结构预留，T08 实现语义）

`WATCH` / `CONFIRMED_FLOW` / `RESEARCH_ENTRY` / `NO_CHASE` / `INVALIDATED`

数据门禁非 `COMPLETE` 时不得进入 `CONFIRMED_FLOW` / `RESEARCH_ENTRY`。  
`earliest_executable_at` 不得早于披露后下一交易日。

## 8. 本版本不包含

- 抓取适配器、回填、标准化引擎、席位名称 NFKC、画像、回测、API、仪表盘、调度与告警（T02–T12）
- 任何实盘下单或仓位管理字段
