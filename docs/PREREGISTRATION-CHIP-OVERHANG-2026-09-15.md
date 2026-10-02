# 预登记：H-20260915-chip-overhang（突破形态 × 筹码结构）

> 状态：**已冻结**（下列哈希在结果生成前写入；脚本/数据未变不得重跑）
> 时间：2026-09-15（Asia/Shanghai）
> 标尺：`docs/STRATEGY-SCORECARD-V0.md`（本任务对应 G2 的条件异质性检验）

## 0. 上游事实（不得隐去）

Phase 1 事件研究（`runtime/research/event-study-v1-full/`，3,244 事件、1,205 只股票、
2021-05-10~2026-09-14）显示：strict 横盘突破对匹配对照的 H20 条件收益差
+0.48%（95% CI [−0.12%, +1.05%]），五个窗口均不显著 → `NO_CONDITIONAL_EDGE`。
本预登记**不否认**该结论；它检验的是"突破形态内部是否存在可由筹码结构识别的
条件异质性"。若本任务失败，形态层仍视为无条件信息。

## 1. 冻结身份

| 项 | 值 |
|---|---|
| hypothesis_id | `H-20260915-chip-overhang` |
| 上游产物 | `runtime/research/event-study-v1-full/diffs.json` SHA-256 `a704026db13496efa51baf2842ba0f49913c69d935c4eb47ca1ae7401334f858` |
| 事件基元库 | `ab_screener/research/event_study.py` SHA-256 `783f866f9a9d0972d91e0bab8fea474fec1faa0c9ffd82c1fb8c9f23ec15a3d9` |
| 分析脚本 | `scripts/chip_conditioning_study.py` SHA-256 `72cffdfcdfe67677cc81ee975b17fcf98a4dc7a21de3fb13cb9b9a10ece62d36` |
| 数据 | `runtime/stock_data.db` 的 `cyq_history`（2022-08-09~2026-08-21，PIT 字段 available_at/revision） |
| 试验预算 | 2 个特征、1 个主 horizon；**跑完不再新增特征** |

## 2. 机制

横盘箱体突破时，筹码分布决定突破后的真实供给：

- **获利盘比例（winner_rate）低**：多数持仓者仍在成本附近或上方，突破初期
  兑现压力小，但若过低也可能意味着上方套牢重（两种方向都写进假设）；
- **成本分散度低**：持仓成本集中，抛压分布窄，突破后回撤更浅。

因此预登记**方向性假设**：突破日 `winner_rate` 越低、成本分散度越低，
“事件 − 匹配对照”的 H20 条件收益差越大。

## 3. 冻结定义

| 项 | 值 |
|---|---|
| 事件 | Phase 1 `diffs.json` 中 H20 有 diff 的事件（次日开盘入场） |
| PIT | 仅当 `cyq_history.available_at ≤ 下一交易日 09:15` 才纳入；`trade_date == signal_date` |
| 主特征 F1 | `winner_rate`（%） |
| 主特征 F2 | `dispersion = (cost_95pct − cost_5pct) / weight_avg` |
| 分组 | 按特征做等频五分位（`pd.qcut(5)`，精确同值合并） |
| 主 horizon | H = 20 |
| 统计量 | D = mean(diff \| Q1) − mean(diff \| Q5)（F1/F2 方向一致：都为"低值更好"） |
| 显著性 | 按 signal_date 聚类的单侧 Bootstrap 95% 下界（2,000 次），两个主检验用 Bonferroni：α=0.025 |
| 单调性 | 五分位均值对分位的 Spearman ρ < 0 |
| PASS | F1 与 F2 同时满足：D>0、单侧下界>0、ρ<0 |
| FAIL | 任一 F 的 D ≤ 0 |
| INSUFFICIENT | 样本 < 300，或方向为正但置信区间跨 0 |

H=5/10/60 仅作描述性报告，不参与裁决，防止事后挑选 horizon。

## 4. 止损

1. 样本 < 300 事件 → 直接 INSUFFICIENT，不补数据、不换特征；
2. 两个特征均 FAIL → 归档，不再在筹码维度上做同类变换（如换分位数、换窗口）；
3. 任何"去掉某段窗口/某类股票后变好"的结果都不得作为 PASS 依据。

## 4b. 修订 1（2026-09-15，在看到任何条件收益结果之前）

预登记 PIT 口径实测为 **0 个可用事件**：`cyq_history.available_at` 记录的是
2026-08-18 / 2026-08-22 的批量入库时间，不是历史决策时点可用时间，按
`available_at ≤ 次日 09:15` 过滤后没有事件能通过。

处置（不影响原假设方向与统计量）：

- 严格 PIT 结果单独落盘 `result_strict_pit.json`，裁决 `INSUFFICIENT_PIT`；
- 新增显式开关 `--allow-unverified-pit`，允许做**不可晋级**的探索分析：
  - `pit_mode=UNVERIFIED_CONTEMPORANEOUS`，假设"筹码值由截止信号日的行情确定"；
  - 裁决强制前缀 `EXPLORATORY_`，`candidate_eligible=false`；
  - 仅用于判断是否值得建设真 PIT 筹码数据，不得写入门禁报告或 A 池。
- 修订发生在看到数据可用性事实之后、任何条件收益结果之前；原特征、方向、
  horizon、显著性阈值均未改动。

## 5. 产物

- `runtime/research/H-20260915-chip-overhang/result.json`（含输入/脚本哈希）
- `runtime/research/H-20260915-chip-overhang/report.md`
- 结论只允许 `PASS_CANDIDATE` / `FAIL` / `INSUFFICIENT`；
  `PASS_CANDIDATE` 也仅进入 shadow 观察，不自动修改每日选股参数、不进入 A 池。
