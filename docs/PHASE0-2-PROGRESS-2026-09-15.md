# Phase 0–2 推进记录（2026-09-14/15）

> 目标：把策略从「不可验证的 2 分」推进到可复算的研究阶梯。
> 本文件只记录已完成的工程与证据，不构成收益结论。标尺见 `docs/STRATEGY-SCORECARD-V0.md`。

## 1. 本轮完成

### 1.1 Phase 0：修复扫描 manifest 竞态（工程）

- 问题：2026-09-14 18:30 自动日跑在「观察池」阶段失败，错误
  `data manifest changed during scan; rerun on a consistent snapshot`，当日无
  `DAILY_COMPLETE` 记录。
- 根因：`run_scan` 在 `load_market_data` **之前**快照 `dataset_partitions` 指纹；
  而加载过程（parquet 缓存预热）会为缺失交易日补写分区。实测 `daily/20260914`
  分区行写于 18:32:57，扫描 18:32:53 已开始 → 审计复核判定"数据变化"。
- 修复：把数据集指纹快照移到 `load_market_data` 与 `as_of` 截断**之后**
  （`ab_screener/screener/orchestrator.py`）。扫描期间任何并发写入仍会被审计拒绝，
  守卫不放宽。
- 回归测试：`tests/test_screener_golden_result.py::test_scan_dataset_version_snapshots_after_cache_partition_write`
  模拟"加载时补写分区"，断言快照包含该写入；修复前必失败。

### 1.2 Phase 1：strict 形态的匹配对照事件研究（证伪）

- 工具：`scripts/event_study_matched.py` + `ab_screener/research/event_study.py`；
  口径=生产检测器 strict（最松台阶 `box_min_days=20`）、次日开盘入场、+H 收盘出场、
  `pct_chg` 连乘、同日同行业最近邻 5 对照、按事件日聚类 bootstrap、伪事件安慰剂。
- 产物：`runtime/research/event-study-v1-full/`（`manifest.json` / `events.json` /
  `stats.json` / `diffs.json` / `report.md`）。
- 样本：1,205 只分层股票 × 261 个采样日（2021-05-10~2026-09-14，step=5）；
  **3,244 个 strict 事件**（1,049 只股票、232 个事件日）。
- 主要结果（事件 − 匹配对照）：

| H | n | 均值 | 95% CI | 为正占比 |
|---|---:|---:|---|---:|
| 5 | 2,992 | −0.06% | [−0.35%, +0.24%] | 45.0% |
| 10 | 2,991 | −0.08% | [−0.52%, +0.37%] | 45.6% |
| 20 | 2,985 | +0.48% | [−0.12%, +1.05%] | 46.1% |
| 60 | 2,965 | −0.64% | [−1.47%, +0.19%] | 44.5% |

- 伪事件零分布：H5 −0.13%、H10 −0.23%、H20 −0.13%、H60 −0.85%（CI 上界均 ≤ +0.22%）。
- 裁决：**NO_CONDITIONAL_EDGE**。形态层相对可比股票没有可检出的条件信息；
  H20 的 +0.48% 不显著且小于/接近伪事件噪声。成本、涨跌停、容量尚未计入。

### 1.3 Phase 2-A：筹码结构条件异质性（预登记 + 数据门禁）

- 预登记：`docs/PREREGISTRATION-CHIP-OVERHANG-2026-09-15.md`（含脚本/输入哈希、
  两个特征、方向、α=0.025、止损）。
- **预登记 PIT 口径直接阻断**：`cyq_history.available_at` 只有
  2026-08-18 / 2026-08-22 两个批量入库时间，按"次日 09:15 前可用"过滤后
  可用事件 = **0** → `INSUFFICIENT_PIT`（`result_strict_pit.json`）。
- 显式探索（不可晋级，`pit_mode=UNVERIFIED_CONTEMPORANEOUS`，2,119 事件）：
  - `winner_rate`（获利盘比例）：预登记方向（低更好）**被反向证伪**——
    Q1 均值 −0.58%，Q5（≈98%）均值 **+2.19%**（CI [+0.80%, +3.78%]），
    Q1−Q5 = −2.77%，分位梯度 ρ=+0.70。方向相反、看似单调，但属于**探索结果**。
  - `dispersion`（成本分散度）：Q1−Q5 = +1.43%，单侧下限 −0.21%，ρ=−0.30；
    方向与假设一致但**不显著**。
- 裁决：`EXPLORATORY_INSUFFICIENT`。不晋级、不改每日参数、不进入 A 池。

## 2. 对评分的影响

| 门禁 | 变化 |
|---|---|
| G0 数据与测量 | 部分：扫描 manifest 竞态已修；筹码/fina 历史 PIT 仍不可用 |
| G1 机制与预登记 | 部分：标尺、模板、Phase 2-A 预登记已建立 |
| G2 条件信息 | **已测**：strict 形态无条件信息（3,244 事件，聚类 CI 跨 0） |
| G3–G8 | 未通过（沿用既有 FAIL/INSUFFICIENT 结论） |

**当前仍为 2/10**：本轮把"不知道有没有 edge"变成了"已用足够样本测得形态层没有
可检出 edge"，属于测量进步而非分数进步。

## 3. 下一步（按顺序）

1. **真 PIT 筹码**：从下一个交易日起按日捕获 `cyq` 快照并记录真实的 available_at
   （或取得供应商版本链），否则筹码维度永远只能探索。
2. **新预登记**：把探索中反向出现的 `winner_rate`（高获利盘 → 更好条件收益）
   写成 H2，用**未看过的时段/未见样本**检验；必须走 `docs/PREREGISTRATION-TEMPLATE-V0.md`，
   禁止直接复用本轮探索样本当验证。
3. **G0 收尾**：行情/PIT 审计、退市与停牌/涨跌停可成交性进入回测；
   清理未提交工作区后重建门禁身份。
4. **G5/G8**：为通过 G2 的假设建立组合层（波动目标/中性化/容量/成本），
   再谈 6 分以上。

## 4. 产物索引

| 文件 | 说明 |
|---|---|
| `docs/STRATEGY-SCORECARD-V0.md` | 2→9 分门禁与止损 |
| `docs/PREREGISTRATION-TEMPLATE-V0.md` | 新假设预登记模板 |
| `docs/PREREGISTRATION-CHIP-OVERHANG-2026-09-15.md` | Phase 2-A 预登记（含修订 1） |
| `runtime/research/event-study-v1-full/` | Phase 1 全量事件研究（本地证据） |
| `runtime/research/H-20260915-chip-overhang/` | Phase 2-A 严格 PIT 阻断 + 探索结果（本地证据） |
| `tests/test_event_study.py` | 事件研究基元回归测试（7 项） |
