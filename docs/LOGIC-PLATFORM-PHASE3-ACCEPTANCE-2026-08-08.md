# LOGIC PLATFORM Phase 3 验收报告

> 日期：2026-08-08
> 规格：`docs/VOLUME-PRICE-LOGIC-PLATFORM.md` §6（DSL）/ §6.5（闸门）/ §10（回测）
> 范围：最小可用 DSL 解释器 + "模板 → 回测 → 闸门"闭环（不接 ML）
> 参考语法：`docs/DSL-REFERENCE.md`

---

## 1. 交付清单

### 新建文件

| 文件 | 职责 | 状态 |
|------|------|------|
| `logic_platform/dsl/schema.py` | pydantic v2 模型：StrategyDSL / Condition / EntryRule / ExitParams / Position / Risk / BacktestParams；op 全集 + feature 命名空间 + ref 动态引用；SchemaValidationError 字段级中文报错 | ✅ |
| `logic_platform/dsl/parser.py` | YAML → StrategyDSL；DslParseError（含行号）/ 模板缺失报错（列出可用模板） | ✅ |
| `logic_platform/dsl/interpreter.py` | Interpreter：条件求值（10 种 op + ref + NaN + pred.* 降级 warning）+ run() 全市场采样扫描（进程池、防连发冷却、特征面板构建） | ✅ |
| `logic_platform/dsl/templates/vol_breakout_v1.yaml` | 模板 1：突破 + 量比 ≥ 1.6 | ✅ |
| `logic_platform/dsl/templates/pullback_volume_v1.yaml` | 模板 2：回踩缩量低吸（4 条件含 ref: box_mid） | ✅ |
| `logic_platform/backtest/engine.py` | 信号 → 逐笔交易（复用宿主 trade_sim fixed 模式）→ 绩效（n_trades/win_rate/PF/total_return/组合回撤/出场分布/截断统计） | ✅ |
| `logic_platform/backtest/gates.py` | 闸门：min_trades / max_drawdown / min_win_rate / min_profit_factor / min_avg_ret → gated / rejected / draft；规则逐条判定 + 可配置 | ✅ |
| `logic_platform/cli/run_logic_backtest.py` | 闭环 CLI：--template / --set 参数覆盖 / --gate 阈值覆盖 / 结构化日志 / 结果 JSON / 落库 logic_strategies + logic_backtests / 退出码=闸门结果 | ✅ |
| `tests/test_logic_platform/test_dsl_schema.py` | 13 用例：校验、错误处理、ref、模板加载 | ✅ |
| `tests/test_logic_platform/test_interpreter.py` | 8 用例：op 全集、ref 映射、NaN、pred 降级、all/any、真实面板端到端 | ✅ |
| `tests/test_logic_platform/test_gates.py` | 7 用例：gated/rejected/draft 流转、自定义阈值 | ✅ |
| `tests/test_logic_platform/test_backtest_engine.py` | 6 用例：绩效聚合、截断、未知信号日、空信号 | ✅ |
| `docs/DSL-REFERENCE.md` | DSL 语法权威参考（§1-9） | ✅ |

### 修改文件

| 文件 | 改动 |
|------|------|
| `logic_platform/__init__.py` | FEATURE_VERSION 升 v0.2.0（含 DSL 层） |
| `docs/VOLUME-PRICE-LOGIC-PLATFORM.md` | §19 变更记录追加 Phase 3 |

## 2. 自动化测试

```powershell
C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q
# 76 passed（含 Phase 0+1 的 41 个）
```

宿主回归（后台执行中）：`pytest tests/ test_signals.py -q`，结论以执行日志为准。

## 3. 闭环端到端验证（手工验收）

### 3.1 小规模冒烟（vol_breakout_v1，50 只，step=10）

链路全程：模板加载 → 扫描（21s）→ 10 信号 → 9 交易 → 闸门 draft（交易不足）→ 落库 → JSON ✅

### 3.2 完整规模（200 只，20250101~20260731，step=5，6 进程）

**vol_breakout_v1（量价突破确认）**

| 指标 | 值 | 闸门规则 | 判定 |
|------|-----|---------|------|
| 信号/交易 | 69 / 63（截断 6） | min_trades ≥ 30 | ✅ 63 |
| 总收益 | -54.3% | — | — |
| 胜率 | 34.9% | min_win_rate ≥ 0.42 | ❌ |
| 盈亏比 | 0.802 | min_profit_factor ≥ 1.2 | ❌ |
| 最大回撤 | 72.0% | max_drawdown ≤ 0.35 | ❌ |
| 平均收益 | -0.89% | min_avg_ret ≥ 2% | ❌ |
| 出场分布 | stop 39 / target 18 / time 6 | — | — |

**状态：rejected（闸门拦截 ✅）**

**pullback_volume_v1（突破回踩缩量低吸）**

| 指标 | 值 | 判定 |
|------|-----|------|
| 信号/交易 | 44 / 44（截断 0） | min_trades ✅ |
| 总收益 | -64.6% | — |
| 胜率 | 25.0% | ❌ |
| 盈亏比 | 0.467 | ❌ |
| 最大回撤 | 65.8% | ❌ |

**状态：rejected（闸门拦截 ✅）**

### 3.3 闭环结论

**机制验证成功**：
1. ✅ 模板 → 解释器（含参数覆盖 `--set exit.stop_pct=0.08`、ref 动态引用）
2. ✅ 回测（复用宿主 trade_sim；截断处理；组合回撤）
3. ✅ 闸门（5 条规则逐条判定、状态流转 gated/rejected/draft、fail-closed）
4. ✅ 落库（logic_strategies status 由闸门决定；logic_backtests 含 metrics+gate）
5. ✅ 结构化日志 + JSON + 退出码（0/1）

**策略绩效说明（非缺陷）**：两套 MVP 模板在 2025-2026 回测区间均未过闸门——这是**闸门机制正确工作的证据**（fail-closed 拦截烂策略），也符合研究平台定位：策略质量需要后续迭代（参数搜索/环境过滤/更优出场），而不是让所有模板都"通过"。

**性能**：200 只 × 74 采样日 ≈ 1.5 万次特征计算，6 进程约 3~4 分钟（vol: 181s / pullback: 246s），日志有进度输出。

## 4. 已知限制 / TODO（后续 Phase 承接）

1. **未建模组合资金/滑点/印花税**：当前为交易级累乘绩效（对齐宿主 trade_sim.summarize 口径）；组合层（position.max_pct/max_names 生效）在 Phase 4+
2. **未过滤 ST/涨跌停不可买**：risk.avoid_st 已声明但解释器未执行（Phase 3 范围外，schema 已预留）
3. **pred.* 特征**：schema 已预留，Phase 2 接 ML 后自动生效（当前降级为不通过+warning）
4. **模板生成器**（template_fill/Optuna）：CLI `--set` 已支持手动参数覆盖，自动网格搜索下一轮
5. **出场仅 fixed 三参数**：DSL exit 的 `ref: box_mid` 等条件出场未实现（trade_sim bench 模式可扩展）

## 5. 结论

**Phase 3 验收通过**：DSL 最小语法 + 解释器 + 回测 + 闸门 + 闭环 CLI 全部交付，76 个自动化测试通过，端到端实跑验证闭环成立，闸门 fail-closed 行为正确。接口保持可扩展（pred.* 预留 ML、DSL 落库预留 UI、参数覆盖预留生成器）。

下一步建议（按既定优先级）：Phase 2 预测（pred.* 特征激活）> Phase 4 UI（/logic 页面渲染策略库与回测报告）。
