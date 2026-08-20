# 策略实验室可信报告与任务恢复验收记录

日期：2026-08-08  
验收范围：净成本 IS/OOS、三窗 Walk-forward、随机与 MA20/60 基线、可信门禁、报告持久化、切页/重启恢复。

## 1. 验收结论

- **实现验收：通过。** 策略实验室已经形成“冻结 IS 第一名 → OOS → 三窗 WF → 双基线 → 门禁 → 一页报告”的确定性流程。
- **本次策略门禁：`INSUFFICIENT_EVIDENCE`。** 系统按失败关闭处理，没有生成可晋级参数，也没有进入 A 池或生成订单。
- `PASS` 仅允许登记到隔离候选区；任何结果都不会自动成为生产参数。
- Lab 任务与报告已落入 SQLite schema v11；切换路由、窗口重新获得焦点和后端重启后均可恢复。

## 2. 真实 full 运行证据

| 项目 | 结果 |
| --- | --- |
| research run | `76c653f1d5fc` |
| 报告 SHA-256 | `0e6f70cdcbfd33691dec47bc5fac75350fac63e13a6e76d83c933235c0477863` |
| 报告冻结版本 | 数据 `8f114fff8bac9e6d`；代码 `76d856838168`；成本 `fd19ff13281945c1` |
| 数据窗口 | IS `20230801–20250731`；OOS `20250801–20260731` |
| 股票池 | 200 只 |
| 参数组合 | 54 组 |
| 固定 IS 第一名 | `487503192dae5950` |
| OOS | 84 笔；净 PF 1.396；净胜率 40.48%；净最大回撤 62.97% |
| WF1 | 32 笔；测试净 PF 0.189；净最大回撤 70.95% |
| WF2 | 0 笔；PF/回撤证据缺失 |
| WF3 | 58 笔；测试净 PF 2.026；净最大回撤 26.73% |
| 随机基线 | 固定种子 `20260808`；83 笔；净均收益 0.6209%；净 PF 1.262 |
| MA20/60 基线 | 83 笔；净均收益 -0.7230%；净 PF 0.787 |
| 最终结论 | `INSUFFICIENT_EVIDENCE`；`candidate_eligible=false` |

人话阻断原因：

1. OOS 净最大回撤 62.97%，超过 25% 门槛。
2. WF1 净最大回撤 70.95%，超过 25% 门槛。
3. WF2 没有成交，三个 WF 窗口的净成本证据不完整。

虽然 OOS 净 PF 和净均收益看上去有吸引力，系统没有据此放宽回撤与完整性门槛。这是预期的可信行为。

报告冻结的是运行当时的代码版本。最终运行实例的 build version 因后续任务进度文案与前端恢复修正变为 `702e924a7ec6`；历史报告不被重写。若研究或成交语义再次变化，必须重新运行 full 门禁，不能把旧报告冒充新版本结论。

## 3. 功能与恢复验收

- `GET /api/lab/status` 在不提供任务 ID 时可恢复活动任务或最近任务。
- 同时启动第二个优化任务返回结构化 `409 LAB_TASK_ALREADY_RUNNING`，包含活动任务 ID。
- 前端进入 `/lab`、切换到其他页面后返回、窗口重新获得焦点或重新可见时，均主动从后端恢复任务。
- 轮询只有一个定时器，完成、失败或取消后停止。
- Markdown 与 JSON 报告均可下载，报告含样本、成本、版本、IS/OOS、WF、双基线、门禁清单与敏感性结果。
- 旧 `active` 参数已隔离为 `legacy_unverified`；周度 arena 不再自动写入 active。
- schema v11 为增量迁移；历史行情、扫描和报告不被删除。

## 4. 新鲜质量证据

以下命令均在最终代码与生产前端构建上重新执行：

```text
C:\Python314\python.exe -m pytest -q
=> 195 passed, 104 warnings

C:\Python314\python.exe -m ruff check .
=> All checks passed!

C:\Python314\python.exe -m mypy paper_trading ab_screener\research web\backend_app.py
=> Success: no issues found in 23 source files

npm --prefix web\frontend run build
=> TypeScript 与 Vite 生产构建成功
```

运行实例：

- build version：`702e924a7ec6`
- schema version：`11`
- research mode：`full`
- `LIVE_TRADING_ENABLED=false`
- SQLite `PRAGMA quick_check=ok`
- SQLite `PRAGMA foreign_key_check`：0 条异常
- OpenAPI：Lab 状态、优化、报告列表、报告详情与下载路径全部存在
- 真实页面：桌面报告可见；390px 视口无横向溢出；控制台错误 0
- 模拟切页/聚焦回归：任务恢复、完成态报告、窄屏与控制台检查全部通过

依赖项仍产生 104 条弃用警告；Vite 对 687.33KB 的 ECharts chunk 给出体积警告。两者不影响本次正确性验收，但应在后续依赖升级和前端 code-split 中处理。

## 5. 安全边界与回滚

- 研究结论不等于买卖建议；可信报告不会自动进入 A 池或订单流程。
- 真实交易开关保持关闭，系统没有新增券商下单路径。
- 回滚前端可恢复上一份 `web/frontend/dist`；后端可回退代码，但 v11 表和列应保留。
- 已落库的研究运行、检查项和报告只追加或标记状态，不删除或改写历史结论。
- 如运行中断，重启后从最近持久化 checkpoint 恢复；不得通过创建新任务绕过已有活动任务。

## 6. 最终判定

本次工程计划已完成并通过实现验收。当前方案 A 的真实 full 结果没有通过可信策略门禁，应继续保持“研究候选不可用”状态；这不是工程失败，而是门禁正确拒绝证据不足的策略结论。
