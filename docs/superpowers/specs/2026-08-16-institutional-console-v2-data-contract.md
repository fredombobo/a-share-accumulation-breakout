# v2.0 数据源、市场情报与 Point-in-Time 合同

| 字段 | 内容 |
|---|---|
| 文档 ID | `PERSONAL-INSTITUTIONAL-V2-DATA-CONTRACT` |
| 状态 | 已批准设计的实现合同；待 P0 验证供应商权限 |
| 时区 | `Asia/Shanghai` |
| 主要适配器 | 根 `tushare_init.py` 提供的受控 Tushare client |
| 禁止 | 裸 requests、第二套 Token/URL、用 ingestion time 伪造历史 available_at |

## 1. 数据等级

- `T0_HARD`：交易日历、instrument、行情、复权、交易规则、公司行为。缺失时阻断相关研究、扫描或账务。
- `T1_STRATEGY`：资金流、估值、财务、行业/主题、指数历史成员。只有声明依赖它的插件被阻断，其他插件可以继续。
- `T2_CONTEXT`：公告、融资融券、榜单等信息展示。权限不足时显示 `INSUFFICIENT`，不得静默填零，也不得进入正式信号。

任何插件的 `required_datasets` 决定其数据门禁；不能因为某个无关 T2 数据缺失而阻断所有扫描，也不能让依赖该数据的插件降级为默认值。

## 2. 数据目录

供应商 method 是适配器当前候选映射，P0 必须用真实 Token 探测权限并固化 capability manifest；供应商变更时只改 adapter，不改领域 ID。

| dataset_id | 等级 | 供应商 capability/method | 业务键 | effective_at | available_at 规则 | 目标新鲜度/深度 | 主要消费者 |
|---|---|---|---|---|---|---|---|
| `trade_calendar` | T0 | `trade_cal` | exchange+cal_date | 交易所日历日期 | 源返回成功时间；历史回填标 `legacy_backfill` | 至少未来/过去日历，历史≥730日 | DAG、窗口、T+1 |
| `instrument_lifecycle` | T0 | `stock_basic`+退市数据 | ts_code+revision | list/delist/change effective date | 源可获取时间；未知历史用保守 ingestion | 每日；全历史生命周期 | as-of universe |
| `daily_ohlcv` | T0 | `daily` | ts_code+trade_date+revision | 当日收盘 | 实际抓取完成时间；不得默认早于收盘 | 最新完成日；≥730日 | 所有插件/估值 |
| `benchmark_daily` | T0 | `index_daily` | index_code+trade_date+revision | 当日收盘 | 实际抓取完成时间 | 000300及全A代理同最新日 | regime/基线/超额 |
| `adj_factor` | T0 | `adj_factor` | ts_code+trade_date+revision | 因子生效交易日 | 源可用/抓取时间 | 与 daily 同覆盖 | 收益/公司行为 |
| `trading_status` | T0 | 停复牌/日线状态 capability | ts_code+trade_date+revision | 状态生效日 | 公告发布时间或抓取时间 | 持仓/订单/A池100% | 可成交判断 |
| `instrument_rules` | T0 | 交易所/产品规则适配 | ts_code+effective_from+revision | 规则生效时点 | 规则公开时点或抓取时间 | 当前100%，历史版本化 | tick/lot/T+1/涨跌幅 |
| `daily_basic` | T1 | `daily_basic` | ts_code+trade_date+revision | 当日收盘 | 实际抓取完成时间 | 声明依赖插件≥98% | 估值/换手/流动性 |
| `moneyflow` | T1 | `moneyflow` | ts_code+trade_date+revision | 当日收盘 | 实际抓取完成时间 | 声明依赖插件≥98% | 资金流/吸筹解释 |
| `fina_indicator` | T1 | `fina_indicator`（仅候选增量） | ts_code+end_date+ann_date+revision | 报告期末 | 公告真实发布时间；缺时间取抓取时间，绝不能取end_date | 候选覆盖100%，历史按公告期 | 财务质量 |
| `corporate_actions` | T0 | 分红送转/除权 capability | ts_code+action_id+revision | ex/record/pay effective time | 公告公开时间或抓取时间 | 持仓100%，历史版本化 | 复权/账本/阻断 |
| `announcements` | T2 | 公告 capability（P0探测） | source_doc_id+revision | 公告描述事件时间 | 供应商 publish_at；缺失用抓取时间 | 候选/持仓尽力100% | 时间线/事件研究 |
| `industry_membership` | T1 | 行业分类适配 | ts_code+taxonomy+effective_from+revision | 成员关系生效日 | 分类公开/抓取时间 | 个股宇宙≥98% | 暴露/分组 |
| `theme_membership` | T1 | 主题分类适配 | ts_code+taxonomy+effective_from+revision | 成员关系生效日 | 分类公开/抓取时间 | 缺失可阻断主题约束 | 主题暴露/搜索 |
| `index_membership` | T1 | 指数成分/权重适配 | index+ts_code+in_date+revision | in/out date | 源公开/抓取时间 | 所用基准全历史 | 历史基准/比较 |
| `margin_detail` | T2 | 融资融券 capability（权限探测） | ts_code+trade_date+revision | 当日 | 实际抓取完成时间 | 可选；缺失INSUFFICIENT | 信息页/实验插件 |
| `market_breadth` | DERIVED | 本地 PIT 派生 | trade_date+universe_hash+version | 当日收盘 | 上游数据全部 available 后 | 每个完成交易日 | Desk/regime/防守层 |

新闻、社交媒体和大模型情绪**不属于 v2.0 正式数据范围**。后续只有建立原文不可变归档、publish_at、revision、授权和防前视测试后才能新增为 T2/T1。

## 3. PIT 字段合同

每个 history record 至少包含：

```text
dataset_id
business_key
effective_at
available_at
ingested_at
source
source_version/revision
content_sha256
raw_manifest_id
```

规则：

1. `effective_at`、`available_at`、`ingested_at` 均为带时区时间；纯交易日字段由适配器映射为明确市场时点。
2. 同业务键新修订追加新行；latest projection 可更新，但 history 不覆盖。
3. 查询接口必须传 `snapshot_id` 或 `decision_at`。
4. 两者同时传入时，若 decision_at 与 snapshot identity 不一致，返回 `SNAPSHOT_DECISION_CONFLICT`。
5. 正式查询只选 `available_at <= decision_at` 的最高可用 revision。
6. 历史回填无法证明真实发布时间时，使用保守 available_at 并标记 `legacy_backfill`；此类数据不能参与要求精确发布时点的研究。

## 4. 原始响应存储合同

本地原始区：

```text
runtime/raw/<dataset_id>/<ingest_yyyy>/<ingest_mm>/<ingest_dd>/<request_sha256>.jsonl.gz
```

- UTF-8 JSON Lines + gzip；记录供应商原字段，不放 Token、请求认证头或个人账户信息。
- 先写同目录随机 `.tmp`，flush/fsync、计算压缩字节和 canonical payload 两个 SHA-256，再原子重命名。
- `raw_ingest_manifests` 保存 request capability、非敏感参数 hash、起止范围、行数、抓取开始/完成、HTTP/供应商状态、两个 hash 和文件相对路径。
- 同 request hash 重放不得重复写内容；不同响应生成新 revision。
- 被 experiment/artifact 引用的原始文件不得自动删除。未引用文件至少保留730日；清理必须生成 compaction manifest 和审计事件。
- 原始区和数据库均不得提交 Git；备份策略必须覆盖 manifest 和被正式研究引用的原始文件。

## 5. 同步与修订流程

```text
capability probe → 请求计划 → 原始原子落盘 → schema/质量校验
→ history append → latest projection → dataset partition hash
→ 下游缓存失效 → data gate → snapshot manifest
```

- Web 启动不执行历史回填。
- `scripts/backfill_pit_v2.py` 按数据集/月分块，checkpoint 可续跑。
- 财务接口禁止全市场逐股循环，只同步候选/持仓或使用供应商批量能力。
- 每个交易日按交易日历 diff 补洞，不能只比较 MAX(date)。
- 修订影响实验数据集时，旧报告保持可读但 identity 不再适用于新运行。

## 6. 数据质量与缺失行为

| 检查 | T0 | T1 | T2 |
|---|---|---|---|
| schema/PIT字段缺失 | FAIL | 依赖插件FAIL | INSUFFICIENT |
| 最新完成日缺失 | FAIL | 依赖插件FAIL | INSUFFICIENT |
| 重复业务键同revision | FAIL | FAIL | FAIL |
| OHLC/量额非法 | FAIL | 不适用 | 不适用 |
| 权限不足 | FAIL（若必需） | 依赖插件INSUFFICIENT | INSUFFICIENT |
| 源端暂时失败 | 不复用旧数据冒充新鲜；FAIL | 插件阻断 | 标记stale |

覆盖要求：活跃 daily ≥98%；持仓、活动订单、A池候选和公司行为 T0 数据100%；插件声明的 T1 数据在其候选集合100%。信息中心必须显示缺失、无权限、stale 和 source version，不得显示0值代替缺失。

## 7. 市场宽度定义

使用当日 PIT 有效个股宇宙，停牌/无有效收盘从价格比例分母排除但单独计数。v1 指标冻结为：

- `advancers` / `decliners` / `unchanged`；
- 60交易日新高/新低家数；
- 收盘高于 MA20/MA60 的有效标的比例；
- 横截面日涨跌幅中位数；
- 全市场成交额与20日均值比；
- 涨停/跌停/停牌家数；
- universe、price-valid 分母和每项 missing count。

均线和新高低使用截至 decision_at 的前复权一致序列。派生记录保存 universe hash、daily partition hashes、formula version 和 available_at。同日同身份重跑 hash 相同。

## 8. 信息中心查询合同

- 搜索支持代码、名称、拼音全拼、拼音首字母、行业和主题。
- 个股档案/timeline/events/breadth 查询必须返回 `snapshot_id`、decision_at、source、revision、effective_at、available_at、ingested_at。
- 默认 snapshot 是服务端最新**已完成且通过数据门禁**的交易日，不是系统当前日期。
- timeline 使用稳定 cursor 分页，默认日期范围1年；最大单页100。
- 缓存键必须包含 snapshot ID、查询参数和数据集 manifest hash。
- 信息页 GET 始终只读，不创建信号、告警或审计业务事件。

## 9. 验收 fixture

每类正式数据至少包含：

1. 正常记录；
2. 决策后才发布的记录；
3. 同业务键两次修订；
4. 无权限/缺失；
5. 源端过期；
6. 原始文件 hash 被篡改。

固定 `decision_at` 重放三次，PIT snapshot、公共特征和消费插件输入 SHA-256 必须一致。源端真实比对至少20标的×5日且零差异；无 Token 时数据检查为 INSUFFICIENT、进程非0、发布总状态 BLOCKED。

