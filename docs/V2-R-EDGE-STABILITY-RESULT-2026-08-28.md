# V2-R 边际稳定性纠错与预登记实验结果

日期：2026-08-28

任务：`V2-P7.5-R-EDGE-STABILITY`

产品：`accumulation_breakout`（AB-Screener，端口 8001）

结论：**工程纠错完成；attack_only 假设被否决；正式 R 仍为 FAIL / NO_CANDIDATE。**

## 1. 已完成的纠错

- `formal-evidence-v2.1.0` 对对齐后的逐日 `<f8` 收益列做精确字节去重；只合并完全
  相同的路径，近似或仅高度相关的路径保持独立。
- 报告同时保存名义参数数、有效路径数、全部重复组、名义矩阵 SHA-256 和有效矩阵
  SHA-256；PBO、嵌套参数选择、试验 Sharpe 离散度和 DSR 使用有效路径。
- 有效路径少于 4 条时正式统计 `INSUFFICIENT`，不得降级到名义参数数。
- `research-market-regime-v1.1.0` 增加有独立身份的 `attack_only`，只允许因果沪深300
  分类器在 `attack` 日产生新入场；默认 `production` 仍允许 attack + neutral。
- 非正 OOS Sharpe 仍计算并展示 DSR；MinTRL 明确为
  `FAIL_NONPOSITIVE_SHARPE`、coverage=0，而不是抛异常并隐藏全部正式统计。
- 新增只读失败诊断命令，不迁移、不修改研究库：

```text
python scripts/diagnose_research_gate.py --db <stock_data.db> --run-id <id> --out <dir>
```

## 2. 原权威运行的口径纠正

对 `v2auth20260828g` 的冻结检查点只读复算：

| 指标 | 原报告 | 精确去重后 |
|---|---:|---:|
| 名义参数 / 有效路径 | 54 / 54 | 54 / 18 |
| PBO | 65.85% | 65.85% |
| DSR | 68.67% | 71.43% |
| MinTRL coverage | 31.8% | 31.8% |
| 嵌套正收益窗 | 2/5 | 2/5 |
| IS 盈利独立路径 | 未显式报告 | 0/18 |

诊断分类为 `TEMPORAL_EDGE_INSTABILITY`。重复试验计数是必须纠正的统计错误，但不是原
策略失败的主因，也不能用来降低门槛。

诊断工件：

- `runtime/v2/research/diagnostics/research_gate_diagnostic_v2auth20260828g.json`
- SHA-256：`dee040510117323d0cce2d136b11f50eab9ffc4ac72633353f28e93a146ee45a`

## 3. attack_only 单次预登记实验

预登记提交：`fe48743`

最终纠错运行：`v2rattack20260828r1`

- 代码版本：`a14549fda40a`
- 数据版本：`ffb32ab0ae2b9856`
- 市况身份：`d2e792a40e37764657d5b03b737a15a5214995a0c8bf610514fc8a3910f91487`
- 研究日：727；允许 attack 243；阻断 neutral + defense 484。
- 名义参数 54；精确独立路径 14；IS 盈利独立路径 0/14。
- 冻结 IS 第一名：`5ea0357601c84bb8`。

| 正式证据 | 结果 | 门槛 | 判定 |
|---|---:|---:|---|
| IS 组合净收益 | -0.5082% | 仅训练选择 | 弱 |
| OOS 组合净收益 | -2.6840% | >0 | FAIL |
| OOS 净 PF | 0.8534 | ≥1.0 | FAIL |
| PBO | 79.65% | ≤20% | FAIL |
| DSR | 28.49% | ≥95% | FAIL |
| MinTRL coverage | 0 | ≥1 | FAIL |
| 嵌套正收益窗 | 2/5 | ≥3/5 | FAIL |
| 2×成本 OOS | -4.5947% | >0 且优于基线 | FAIL |
| 参数邻域覆盖 | 100% | 100% | PASS |

数据库终态：`done / DONE / 100 / FAIL`，`candidate_eligible=false`，
`can_claim_edge=false`；正式候选为 `NO_CANDIDATE`。

首轮 `v2rattack20260828a` 暴露了非正 Sharpe 的 MinTRL 呈现缺陷，原记录未删除。修复后
使用完全相同配置纠错重跑。两轮的市况身份、数据版本、冻结参数、IS/OOS 收益、OOS 权益
哈希、PBO、五个嵌套窗、有效矩阵哈希和 2×成本结果逐项一致；只有代码身份和负收益统计
呈现不同。

最终报告：

- `runtime/v2/research/trusted_report_v2rattack20260828r1.json`
- 数据库报告 SHA-256：`0daf069650676c133c2e4943ff539d87e028c3cedc5ac1856c0df6da9cef1646`
- 诊断 SHA-256：`956c51aa249a8b551695f7627686566069494d5cf2eab1e1f61620a900d20ab4`

## 4. 裁决与下一步边界

- `attack_only` 已按预登记停止规则被否决，不再测试其它收益阈值、均线窗口或状态组合。
- 本实验是在看过旧 OOS 汇总后提出；即使它通过，也不能直接清除 R。本次实际为 FAIL，
  因而不存在候选晋级争议。
- 当前权威配置仍保留 `v2auth20260828g` 的正式 FAIL，不把更差的实验运行替换成权威身份。
- 后续若继续 R，必须提出新的经济机制和新的预登记任务；不得围绕本次 OOS 调市场阈值。
- 生产扫描、A/B 池、纸面订单、风险阈值和 `LIVE_TRADING_ENABLED=false` 均未修改。

## 5. 验收证据

- 提交 `f032423`：精确路径去重、attack_only PIT 身份、只读诊断与测试。
- 提交 `7fea91c`：非正 Sharpe 的 DSR/MinTRL fail-closed 呈现。
- 首轮严格质量门：Ruff、Mypy、严格架构、946 个离线测试和前端构建通过。
- 负 Sharpe 纠错后相关 Ruff、Mypy 和 12 个回归测试通过。
- 结果文档提交后最终严格质量门通过：Ruff、Mypy、严格架构、947 个离线测试和前端构建
  全部 PASS；ECharts 687KB 分包警告为非阻断既有性能项。
