# V2-R 进攻环境突破假设预登记

日期：2026-08-28

任务：`V2-P7.5-R-EDGE-STABILITY`

产品：`accumulation_breakout`（AB-Screener，端口 8001）

状态：**PREREGISTERED / NOT YET TESTED**

## 1. 已知事实与问题

权威运行 `v2auth20260828g` 在生产一致的因果市场环境过滤、PIT 数据、组合会计和净成本下：

- OOS 组合净收益 11.01%，但 IS 组合净收益 -6.30%；
- CSCV-PBO 65.85%；
- DSR 68.67%；
- MinTRL coverage 31.8%；
- 五个真正嵌套外层窗仅 2/5 为正；
- 54 个名义参数只有 18 条精确不同的收益路径，每条路径被三个无效的 `exit_window`
  取值重复表示。

精确去重的只读诊断显示：PBO 与嵌套窗结果不变，DSR 仅约升至 71.43%。因此，重复参数
计数必须纠正，但不能解释或掩盖策略的跨阶段不稳定。

## 2. 单一经济假设

横盘吸筹突破需要广泛风险偏好配合。在沪深300仅处于“中性”状态时，个股突破更容易是假
突破；只有生产分类器定义的“进攻”状态才允许该实验策略产生新入场信号。

固定定义：

- 基准：`000300.SH` 的冻结 PIT 日线；
- 分类器：继续复用 `breakout-market-regime-v1.0.0`；
- 进攻：指数不低于 MA20 容忍带，且 20 日收益不低于 2%；
- 实验入场允许集：仅 `attack`；
- `neutral` 与 `defense` 都阻止该实验策略新开仓；
- 只限制新信号日期，不修改既有持仓的退出、估值或会计。

## 3. 固定实验设计

- 策略：A（strict accumulation breakout）。
- 数据：与 `v2auth20260828g` 相同的冻结 PIT 快照和历史生命周期宇宙。
- 窗口：自动 full 窗；IS 20230801~20250731，OOS 20250801~20260731。
- 样本：600 标的，步长 10。
- 参数：保持原网格，不新增阈值；正式统计对精确相同收益路径只计一次独立试验。
- 选择：仅在 IS 选择；OOS、嵌套测试折和 2×成本测试不参与选择。
- 基线：随机与 MA20/60，使用完全相同的 `attack_only` 可用信号日期。
- 费用、滑点、参与率、T+1、组合权重与现金限制全部不变。
- 晋级阈值不变：PBO≤0.20、DSR≥0.95、MinTRL coverage≥1、五窗至少 60% 为正、
  2×成本、参数邻域和身份完整性全部通过。

## 4. 污染披露与裁决边界

该假设是在看到旧策略的总体 OOS 结果后提出，因此 20250801~20260731 不是全新的未触碰
holdout。本轮运行只能作为**二级确认性证据**：

- FAIL：立即否决该假设，不再尝试其它市场阈值；
- 历史门槛全部 PASS：只进入隔离观察，不直接改写权威 R=PASS；
- 最终清除 R 仍需使用 20260801 之后未参与本假设形成的真实数据/影子观察，并满足正式样本要求。

不得把本实验的历史 PASS（如有）描述为全新 OOS，也不得自动进入 A 池或生成纸面买单。

## 5. 停止规则

- 只运行 `attack_only` 一个新策略变体；不测试其它收益阈值、均线窗口或状态组合。
- OOS 成交少于 30、正式矩阵少于 4 条独立路径、PIT 缺失或任一身份不匹配均为
  `INSUFFICIENT`。
- 任一正式硬门失败即 `NO_CANDIDATE`；不降低阈值、不删除失败窗、不更换主基线。
- 旧报告与失败记录保持不可变。

## 6. 实现影响文件

- `ab_screener/research/formal_evidence.py`
- `ab_screener/research/trusted_run.py`
- `ab_screener/research/regime_filter.py`
- `scripts/run_trusted_research_real.py`
- `ab_screener/research/failure_diagnostics.py`
- `scripts/diagnose_research_gate.py`
- 对应单元、集成、PIT 和契约测试

生产扫描、纸面账本、风险阈值、订单状态机及 `LIVE_TRADING_ENABLED=false` 不在本任务修改范围。
