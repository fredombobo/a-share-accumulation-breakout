# Strategy DSL 参考（Phase 3 最小可用版）

> 位置：`logic_platform/dsl/`（schema.py / parser.py / interpreter.py / templates/）
> 与主文档 `VOLUME-PRICE-LOGIC-PLATFORM.md` §6 对齐；本文件是语法权威参考。

## 1. 一句话

DSL 是把「量价特征 + 结构状态」翻译成**可回测策略**的声明式语言（YAML/JSON 等价）。
LLM / 生成器只允许改 DSL，不允许直接改生产 Python（docs §6.1）。

## 2. 最小语法

```yaml
strategy:                 # 必填：模板声明
  id: vol_breakout_v1     # 必填：策略唯一 id
  version: "1.0.0"        # 语义化版本
  name: 量价突破确认        # 必填：中文名
  research_only: true     # 默认 true：未过闸门禁止进纸交易

params:                   # 回测参数（CLI --set 可覆盖）
  start: "20250101"       # 回测起点 YYYYMMDD
  end: "20260731"         # 回测终点
  step: 5                 # 采样日步长（交易日）
  max_codes: 200          # 扫描股票数上限
  lookback_bars: 180      # 特征回看 K 线数
  workers: 4              # 进程数

entry:                    # 入场规则
  all:                    # 全部满足（AND）
    - { feature: "structure.state", op: "in", value: ["BREAKOUT", "FOLLOW_THROUGH"] }
    - { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }
    - { feature: "close", op: ">=", ref: "box_mid" }   # ref：动态引用
  any: []                 # 任一满足（OR），空组视为通过

exit:                     # 出场参数（映射宿主 trade_sim fixed 模式）
  stop_pct: 0.07          # 止损比例
  target_pct: 0.12        # 止盈比例
  max_hold: 15            # 最长持有交易日

position:                 # 仓位（MVP 仅记录）
  method: fixed_pct
  max_pct: 0.10
  max_names: 15

risk:                     # 风控
  avoid_st: true
  regime_block: false
```

## 3. 条件表达式

每个条件：`{feature, op, value | ref}`

### op 全集

| op | 语义 | value 要求 |
|----|------|-----------|
| `>=` `<` `>` `==` `!=` | 数值比较（NaN 参与比较 → 条件不通过） | 数值 |
| `in` / `not_in` | 列表包含 | 列表 |
| `is_nan` / `not_nan` | 缺失判断 | 无 |

### feature 命名空间（Phase 3）

| 前缀 | 可用特征 |
|------|---------|
| `structure.` | `state`（IDLE/ACCUMULATION/TIGHTENING/BREAKOUT/FOLLOW_THROUGH/FAIL）、`is_breakout`、`box_high/low/mid/amp/days/quality`、`days_from_box_end`、`breakout_date` |
| 量能 | `vol_ma_ratio_5_20`、`vol_percentile_60`、`shrink_days`、`breakout_vol_mult`、`amount_ratio`、`vp_corr_20` |
| 价格 | `ret_1/5/20`、`atr_14`、`dist_ma20/60`、`dist_high_60`、`close`、`vol` |
| `pred.*`（预留 Phase 2） | `pred.p_up_5/10/20`：ML 未启用时视为缺失 → 条件不通过 + warning |

### ref 动态引用

`ref` 与 `value` 二选一：`box_mid` / `box_high` / `box_low` / `ma5` / `ma10` / `ma20`。
解释器从特征面板实时取值（如 `close >= ref: box_mid` = 收盘站上箱体中轴）。

## 4. 语义（解释器如何执行）

1. 对回测区间每个**采样日**（step 步长）：
   - 取截至该日 `lookback_bars` 根 K 线 → 跑宿主 `signals.detect_accumulation_breakout`
     （唯一箱体计算源，本平台只适配不重算）
   - 计算特征 + 状态机 → 构建特征面板
   - 求值 `entry.all`（全过）且 `entry.any`（任一，空组通过）→ 命中信号
2. **防连发**：同股票信号日间隔 < 5 个交易日则跳过
3. 信号 → `backtest/engine` 逐笔 `trade_sim.simulate_trade(mode="fixed")`
   （信号日**次日开盘**入场，参数取 `exit` 段）
4. 绩效 → `gates.evaluate`（见下）

## 5. 闸门（backtest/gates.py）

| 规则 | 默认阈值 | 说明 |
|------|---------|------|
| `min_trades` | 30 | 不足 → status=draft（degraded，禁止上架） |
| `max_drawdown` | 0.35 | 超限 → rejected |
| `min_win_rate` | 0.42 | 低于 → rejected |
| `min_profit_factor` | 1.2 | 低于 → rejected |
| `min_avg_ret` | 0.02 | 低于 → rejected |

全部通过 → `gated`（仍 research_only）。CLI `--gate min_trades=20 --gate max_drawdown=0.4` 可覆盖。

## 6. 错误处理

| 错误 | 抛出点 | 示例信息 |
|------|--------|---------|
| `DslParseError` | parser | `DSL 语法错误 t.yaml（第 3 行）: mapping values are not allowed here` |
| `SchemaValidationError` | schema | `entry.feature 'foo' 不支持（可用: ...）`；`entry.op 'bogus' 不支持`；`op 'in' 要求 value 为列表`；`op '>=' 要求提供 value 或 ref`；`strategy.id 必填` |
| `FileNotFoundError` | parser | `模板不存在: x.yaml（可用: ['vol_breakout_v1', ...]）` |
| `InterpreterError` | interpreter | 运行时兜底（正常路径已被 schema 拦截） |

## 7. 内置模板

- `vol_breakout_v1`：状态 ∈ {BREAKOUT, FOLLOW_THROUGH} 且 量比 5/20 ≥ 1.6
- `pullback_volume_v1`：状态 ∈ {FOLLOW_THROUGH, TIGHTENING} 且 缩量 ≥ 3 日 且 量能分位 ≤ 0.5 且 收盘 ≥ 箱体中轴

## 8. CLI

```powershell
# 跑通闭环（模板 → 回测 → 闸门 → 落库）
C:\Python314\python.exe -m logic_platform.cli.run_logic_backtest --template vol_breakout_v1
# 参数覆盖 + 闸门放宽 + 输出路径
C:\Python314\python.exe -m logic_platform.cli.run_logic_backtest --template pullback_volume_v1 `
  --max-codes 200 --step 5 --workers 6 `
  --set exit.stop_pct=0.08 --set exit.target_pct=0.15 --set exit.max_hold=20 `
  --gate min_trades=20 --json runtime/logic_bt_result.json
```

退出码：闸门通过 = 0，未通过 = 1（便于脚本化）。

## 9. 扩展点（后续 Phase）

- **Phase 2 ML**：`pred.*` 特征面板接入模型输出即可，DSL 无需改语法
- **Phase 4 UI**：`dsl_yaml` 已落库 `logic_strategies`，前端直接渲染/编辑
- **生成器**：`template_fill`（参数网格/Optuna）只需改 `params`/`exit` 段后调同一条回测链路
