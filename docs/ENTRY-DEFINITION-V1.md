# A 池入场定义 v1（冻结）

> 状态：**冻结** · 2026-08-08  
> 代码真相源：`ab_screener/domain/entry_definition.py`  
> ID：`A_POOL_STRICT_NEXT_OPEN_V1`

本文件冻结「什么叫一笔可研究的 A 池形态入场」。  
**扫描总览 A 池、轻量回测、假突破归因、可信证据报告、纸面信号入场** 必须共用本定义。  
Lab 网格只允许改**出场参数/阈值**，不得改入场 timing 或另写一套「采样日买入」。

---

## 1. 一句话

> **strict 横盘吸筹箱体 + 近窗放量突破确认 → 突破日下一交易日开盘入场**

---

## 2. 信号（形态层）

| 项 | 规则 |
|----|------|
| 引擎 | `signals.detect_accumulation_breakout`（默认 strict） |
| 箱体 | 约 1–6 个月（`BOX_MIN_DAYS`–`BOX_MAX_DAYS`），稳健振幅、结构触及、拒单边通道、位置/趋势过滤 |
| 突破窗 | 最近 `BREAKOUT_WINDOW_DAYS=5` 个交易日 |
| 突破 | 收盘有效突破阻力 + 放量 + 涨幅适中 + 站稳 + MA 多头 |
| A 池形态 | `is_breakout == True` |

扫描路径另叠加：资金流、基本面、主题软加分、**防守环境清空 A 池**。  
**回测/归因默认只验形态层 + 固定出场**，资金/防守可作为开关另报（见证据报告）。

---

## 3. 入场（执行层）

| 项 | 规则 |
|----|------|
| 信号日 | `breakout_date` 对应 K 线（`signal_index`） |
| 入场日 | **信号日下一交易日**（`entry_index = signal_index + 1`） |
| 入场价 | 入场日 `open`；缺失则 `close` |
| 禁止 | 用「采样日 + 1」代替突破日 + 1 |
| 无法入场 | 无下一根 K 线 → 丢弃该信号 |

代码：

```python
from ab_screener.domain.entry_definition import resolve_entry_from_signal, entry_price_from_bars

resolved = resolve_entry_from_signal(bars, sig)
# simulate_trade(bars, resolved["signal_index"], ...)  # trade_sim 内部再 +1 取开盘
```

`trade_sim.simulate_trade(bars, entry_i, …)` 的 `entry_i` **语义是信号日索引**（历史命名），内部用次日开盘——与本定义一致。

---

## 4. 出场默认（研究基线，可网格）

### fixed（形态基线）

- 止损：入场价 × (1 − 7%)
- 止盈：入场价 × (1 + 12%)
- 最长持有：15 交易日
- 优先级：止损 → 止盈 → 时间

### bench（标杆量出场，Lab 主网格）

- 止损 7% 兜底；标杆量二次出货；最长 30 日
- 优先级：止损 → bench → 时间

成本后口径见 `ab_screener.domain.costs`（佣金/印花税/滑点/一字板）。

---

## 5. 池与环境

| 池 | 来源 | 可否当主推 |
|----|------|------------|
| A | 仅 strict | 候选，仍需人工 |
| B | relaxed / theme_fill | 观察，禁止与 A 混排 |

防守 regime：A 池强制为空（禁止新开仓）。

---

## 6. 消费者清单（必须对齐）

| 模块 | 用法 |
|------|------|
| `run_screener` / Web 扫描 | strict → A；展示 breakout_date |
| `backtest_signals` | `resolve_entry_from_signal` + 次日开盘 |
| `run_attribution` | 同入场；前向 5/10/20 日收益 |
| `run_evidence_report` | 同入场 + 成本后 IS/OOS |
| `trade_sim` | `entry_i` = 信号日 |
| Lab / `optimizer` | 不得改 ENTRY_TIMING |
| 纸交易 | 收盘信号 → 下一可交易日开盘尝试成交（与 costs 一致） |

---

## 7. 变更流程

1. 升版本号为 v2，**禁止静默改 v1 语义**  
2. 同步改 `entry_definition.py` + 本文件 + 回归测试  
3. 旧报告标注 `entry_definition_id`，不可与 v2 混比  

---

## 8. 非目标

- 本定义不保证收益 / edge  
- Lab 排行榜 ≠ A 池名单  
- 未过 `research_mode=full` + 净成本 OOS/WF 门禁前，禁止「可下单参数」话术  
