# v2.0 六形态策略目录与插件合同

| 字段 | 内容 |
|---|---|
| 文档 ID | `PERSONAL-INSTITUTIONAL-V2-STRATEGY-CATALOG` |
| 状态 | 六个 EXPERIMENTAL 起始规格；不代表策略有效 |
| 选股范围 | PIT 历史 A 股个股宇宙 |
| 决策/成交 | 收盘数据全部 available 后决策；最早下一可交易日开盘 |
| 自动交易 | 禁止；只有 `ACTIVE_FOR_A_POOL` 可生成待人工确认草稿 |

## 1. 核心分离

每次结果同时保存两个独立定义：

- `strategy_definition_id/hash`：为什么选中；
- `execution_timing_definition_id/hash`：何时及如何尝试成交。

共享执行时点定义为 `NEXT_TRADABLE_OPEN_EXECUTION_V1`。旧 `A_POOL_STRICT_NEXT_OPEN_V1` 通过兼容 adapter 映射为“吸筹突破 selection V1 + next-open execution V1”；不能让超跌反转、趋势回踩等插件冒用吸筹突破 ID。

## 2. 公共指标定义

所有滚动窗口只使用 `decision_at` 前已 available 的交易日；标记 `shift(1)` 时明确排除决策日。

- `SMA_n(x)`：最近 n 个有效交易日简单平均；不足 n 返回 missing。
- `RET_n = close_t / close_{t-n} - 1`。
- `TR_t = max(high-low, abs(high-prev_close), abs(low-prev_close))`。
- `ATR14 = SMA_14(TR)`；`ATR_PCT14 = ATR14 / close`。
- `VOL_RATIO_n = volume_t / SMA_n(volume shifted 1)`。
- `BB_WIDTH20 = 4 × population_std20(close) / SMA20(close)`。
- `RSI14`：Wilder smoothing，首值为前14日平均 gain/loss；全零 loss 时为100，全零 gain/loss 时为50。
- `PRIOR_HIGH_n = max(high_{t-n} ... high_{t-1})`，不含当日。
- `ROBUST_RANGE_n = (q90(high shifted1) - q10(low shifted1)) / median(close shifted1)`，quantile 使用线性插值。
- `LINEAR_SLOPE_NORM_n = OLS_slope(close shifted1) × n / median(close shifted1)`。
- `RS_n = RET_n(stock) - RET_n(000300.SH)`，内部以小数保存；展示时乘以100并标为百分点。

缺值不前向填充；停牌日不伪造量价。复权序列和公司行为必须引用同一 PIT snapshot。

## 3. 公共预筛 `A_SHARE_LIQUID_V1`

| 条件 | 默认 | 搜索/覆盖 |
|---|---:|---|
| instrument type | A股个股 | 固定 |
| 上市交易日 | ≥120 | 60/120/250 |
| as-of ST | false | 固定硬门 |
| 最近120日有效行情 | ≥95% | 固定硬门 |
| ADV20成交额 | ≥5000万元 | 2000/5000/10000万元 |
| 收盘价 | ≥3元 | 2/3/5元 |
| instrument rule | 必须存在 | 固定硬门 |

ADV 使用统一人民币金额单位，不直接使用供应商原始单位。预筛不查看下一日是否停牌/涨停；下一日可成交性由执行核心决定。

## 4. 插件治理状态

```text
EXPERIMENTAL → REJECTED | CANDIDATE → SHADOW
SHADOW → ACTIVE_FOR_A_POOL | REJECTED
ACTIVE_FOR_A_POOL → RETIRED
```

- `EXPERIMENTAL`：可在 Lab/实验扫描运行，不进入正式 A/B 池。
- `CANDIDATE`：ROBUST_PERSONAL_V2 通过，等待影子观察。
- `SHADOW`：进入 Monitor/WATCHING，不生成订单。
- `ACTIVE_FOR_A_POOL`：同一 identity 的研究、Shadow、Paper、数据和风险门禁均通过；可产生待人工确认草稿。
- `REJECTED/RETIRED`：只读保留历史。

工程 v2.0 的可交付底线是六插件全部达到 EXPERIMENTAL 契约和测试；不能承诺真实数据一定使六个插件通过统计门禁。

## 5. S1 吸筹突破

| 项 | 定义 |
|---|---|
| strategy_id | `ACCUMULATION_BREAKOUT_SELECTION_V1` |
| 假设 | 长期横盘、筹码稳定并伴随可验证资金/量价改善后，突破可能具有短中期延续 |
| 实现来源 | 冻结 `docs/ENTRY-DEFINITION-V1.md` 和现有 golden；不得在本文重写后悄然改变 |
| 必需数据 | daily、moneyflow（若 profile 声明）、instrument/rules、regime |
| lookback | 20–125日箱体，执行 profile 当前 resolved config |
| entry | 决策日 strict 突破，下一可交易日开盘执行 |
| outcome | 5/10/20交易日净成本超额 |
| 失效 | 数据/PIT缺失、通道趋势而非箱体、无量突破、不可交易、防守overlay |

实施步骤：从现有逻辑提取纯插件，生成 `configs/strategies/accumulation_breakout_v1.yaml` 的完整 resolved defaults/search ranges，并以 V1 golden 证明零语义变化。任何 MA60、回踩次数等新增条件进入新的 selection ID，不修改 V1。

## 6. S2 缩量收敛后突破

| 参数 | 默认 | 允许搜索 |
|---|---:|---|
| contraction history | 120日 | 90/120/160 |
| BB_WIDTH20 历史分位 | ≤20% | 10/20/30% |
| ATR_PCT14 历史分位 | ≤30% | 20/30/40% |
| 收敛期量比 `SMA5/SMA20` | ≤0.75 | 0.6/0.75/0.9 |
| breakout lookback | 20日 | 20/40/60 |
| breakout buffer | 0.5% | 0/0.5/1% |
| breakout volume ratio | ≥1.5 | 1.2/1.5/1.8 |

精确条件：在 `t-5..t-1`，BB_WIDTH20 与 ATR_PCT14 的中位数分别不高于过去120日的指定分位，且量能收缩；决策日 `close_t >= PRIOR_HIGH_n × (1+buffer)`，`VOL_RATIO20 >= threshold`，收盘非一字涨停。信号只在收盘数据 available 后生成。

失效：收敛期价格向下破位、决定日无量/一字板、缺120日历史、regime 防守。

## 7. S3 趋势回踩再启动

| 参数 | 默认 | 允许搜索 |
|---|---:|---|
| 趋势 | close>MA20>MA60 | 固定结构 |
| MA60 20日斜率 | ≥2% | 0/2/4% |
| 回踩观察 | 最近10日 | 5/10/15 |
| 从20日高点回撤 | 3%–12% | 2–8/3–12/5–15% |
| 靠近MA20 | low≤MA20×1.03 且 close≥MA20×0.98 | 距离2/3/5% |
| 回踩量比 | ≤0.8 | 0.6/0.8/1.0 |
| 再启动 | close>前3日最高价 | 2/3/5日 |
| 再启动量比 | ≥1.2 | 1.0/1.2/1.5 |

回踩量比使用回踩窗口平均量/此前20日平均量；不能用决策日放量覆盖回踩期。失效：MA20跌破MA60、回撤超过上限、支撑破位、数据不足或下一开盘不可成交。

## 8. S4 放量平台突破

| 参数 | 默认 | 允许搜索 |
|---|---:|---|
| 平台窗口 | 60日 | 40/60/90/120 |
| ROBUST_RANGE | ≤18% | 12/18/24% |
| 支撑/压力触及 | 各≥2次 | 2/3/4 |
| 触及容差 | 2% | 1/2/3% |
| `abs(LINEAR_SLOPE_NORM)` | ≤8% | 5/8/12% |
| OLS R² | ≤0.40 | 0.25/0.40/0.60 |
| breakout buffer | 0.5% | 0/0.5/1% |
| volume ratio | ≥1.5 | 1.2/1.5/2.0 |

支撑/压力分别使用 shifted 平台 low 的 q10 与 high 的 q90；触及事件需相隔至少3个交易日。决策日收盘突破压力且放量。与吸筹突破的区别：本插件只描述价格平台/量能，不使用资金流或筹码改善作为硬条件。

## 9. S5 超跌反转

| 参数 | 默认 | 允许搜索 |
|---|---:|---|
| 60日高点回撤 | ≤-20% | -15/-20/-25% |
| 最近5日最低 RSI14 | ≤30 | 20/30/35 |
| 止跌窗口 | 3日无新低 | 2/3/5 |
| 反转确认 | close>MA5 且 close>前一日high | MA5/MA10；1/3日高 |
| volume ratio | ≥1.2 | 1.0/1.2/1.5 |

该形态与趋势策略风险不同，初始只能 EXPERIMENTAL/WATCHING。即使未来激活，单票目标权重上限默认为5%，需要独立风险 profile。失效：继续创新低、公司行为造成伪跌幅、无量反弹、ST/退市风险或缺公司行为数据。

## 10. S6 相对强势新高突破

| 参数 | 默认 | 允许搜索 |
|---|---:|---|
| RS120 vs 000300 | ≥15个百分点 | 10/15/20pp |
| RET60 | >0 | 固定 |
| 趋势 | close>MA20>MA60 | 固定 |
| MA60 20日斜率 | >0 | 0/2/4% |
| 新高窗口 | 60日 | 40/60/120 |
| breakout buffer | 0.5% | 0/0.5/1% |
| volume ratio | ≥1.3 | 1.0/1.3/1.6 |

决策日 `close_t >= PRIOR_HIGH_n × (1+buffer)` 且满足相对强势、趋势和量能条件。基准必须使用同一交易日和 PIT snapshot；缺基准时返回 INSUFFICIENT，不以0超额替代。

## 11. 防守观察 overlay

`DEFENSIVE_REGIME_OVERLAY_V1` 不产生选股信号。以下三项满足至少两项时进入 DEFENSIVE：

1. 沪深300收盘低于MA20；
2. 沪深300收盘低于MA60；
3. 当日有效宇宙中高于MA20比例 <35%。

ATTACK：沪深300高于MA20和MA60，且高于MA20比例 ≥55%；其他为 NEUTRAL。DEFENSIVE 阻断所有新策略买入草稿，现有卖出和风险降低操作不受阻；命中形态只进入 WATCHING。

overlay version、breadth universe hash 和 benchmark snapshot 必须写入信号解释。

## 12. A/B 与订单关联

- A池：plugin=`ACTIVE_FOR_A_POOL`、signal=`TRADEABLE`、overlay允许开仓、数据/流动性/风险全部通过。
- B池/观察：ACTIVE plugin 的软条件不足，或 SHADOW 插件的观察信号；永不自动生成买单。
- EXPERIMENTAL：只在 Lab/策略库显示，不混入正式总览 A/B。
- 策略订单必须保存精确 `signal_observation_id`；组合策略保存预登记 `composite_decision_id`；人工历史练习保存 `manual_exercise=true`。

同一股票多插件命中时保留全部 observations，不相加分数。订单由用户明确选择一个 ACTIVE observation，或选择已经通过独立门禁的 composite strategy；禁止按“最近同代码信号”猜测。

## 13. 信号生命周期和期限

```text
OBSERVED → QUALIFIED → WATCHING | TRADEABLE
WATCHING → TRADEABLE | EXPIRED | INVALIDATED
TRADEABLE → ORDER_CREATED | EXPIRED | INVALIDATED
ORDER_CREATED → CONFIRMED | CANCELLED | REJECTED | EXPIRED
CONFIRMED → ENTERED | CANCELLED | REJECTED | EXPIRED
ENTERED → EXITED
```

默认 signal validity 为5个**交易日**，从 signal_at 后第一个交易日开始计数；plugin 可在预登记中缩短但不能运行时延长。部分成交产生 ENTERED（记录 filled quantity）并让剩余订单按 DAY 规则过期；它不修改原始 observation。

## 14. Outcome 合同

- 5/10/20 均为理论入场日后的第 N 个交易所交易日。
- 理论入场使用统一执行核心在下一可交易日开盘尝试；有效期内始终不可成交则 `UNFILLABLE/EXPIRED`，return 为 NULL。
- 终点为第 N 日 PIT 调整后收盘估值；报告 gross、1×成本 net 和相对沪深300/全A超额。
- 公司行为、停牌和缺行情产生结构化状态，不能前向填充或用0收益代替。
- Outcome 是理论研究结果；纸面 fill/outcome 单独记录，不互相覆盖。

## 15. 每个插件的最低测试包

六插件分别必须具有：

1. 一个命中 golden fixture；
2. 一个关键条件不命中 fixture；
3. 一个未来 `available_at` 被拒绝 fixture；
4. 一个停牌/涨跌停/无量执行 fixture；
5. 一个插件异常隔离 fixture；
6. 一个参数版本/golden hash 兼容测试；
7. 一份独立 research status 和真实数据结论。

新增或修改公式必须新建 strategy version、更新本目录、YAML、golden 和实验登记。不得为了让真实回测通过而事后改默认值。
