# V2R-Q2 集成质量债务清零交付

日期：2026-08-27  
范围：Breakout v2 最终集成工作树；不接触 AETF、生产数据库或真实下单开关。

## 结论

Q2 工程质量门禁通过。原始失败集合已从 Ruff 86 项、Mypy 15 项、Pytest 3 项失败与
8 项 warning，收敛到 Ruff 0、Mypy 0、完整 Pytest 879 项通过且无 warning 汇总。
未扩大 lint/type ignore，未删除失败测试，未改变策略晋级阈值或研究 FAIL 结论。

## 主要纠错

- 分开提交机械 import/lint 清理与真实 typing/语义修复。
- 测试启动统一使用一次性完全迁移 SQLite，杜绝读取工作树或生产运行库。
- 修正 baseline manifest 测试对 ignored runtime 文件的隐式依赖。
- 修正 retry 空异常、结构化风险 details、DSL 字符串等值、研究参数与 review
  attribution 类型边界。
- 清除 pandas concat 和 Windows joblib 物理核心探测 warning。
- 前端测试客户端与 Vitest 升级后，Node 审计从 5 项漏洞降为 0。
- 新增实际可收集的 `performance` 用例：健康/Desk、100 候选 Overview、100 持仓、
  1000 订单分页；总览迷你图缩为 10 个交易日，完整 K 线仍由详情接口提供。
- 订单查询新增兼容 `offset` 分页和无额外 `COUNT(*)` 的 look-ahead `has_more`。

## 验收证据

使用 `E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`：

| 门禁 | 结果 |
|---|---|
| `python -m pytest -q` | `879 passed in 251.83s` |
| `python -m pytest -q -m performance` | `3 passed, 876 deselected` |
| `python -m pytest -q -m fault_injection` | `18 passed, 861 deselected` |
| `python -m ruff check . --exclude web/frontend/node_modules` | PASS |
| `python -m mypy ab_screener paper_trading logic_platform web/backend_app.py` | PASS（239 files） |
| `python scripts/check_architecture.py --strict` | PASS |
| `npm --prefix web/frontend run test` | 6 passed |
| `npm audit` | 0 vulnerabilities |

`tests/test_performance_v2.py` 是确定性的临时库回归预算，只能证明代码没有明显性能
退化。P8 仍必须在固定机器与真实冻结数据快照上生成含 p50/p95/p99、冷热缓存、代码、
配置、数据、并发和机器身份的独立报告；本用例不得冒充生产性能证书。

## 未改变的阻断

- 权威研究任务 `0746a4108e15` 仍为 `FAIL`，不得晋级或描述为可交易策略。
- O 闸门五个真实完成交易日观察仍为 `INSUFFICIENT`，不能压缩或伪造。
- `LIVE_TRADING_ENABLED` 保持 `false`。

## 回滚

本任务仅包含质量修复、测试隔离、查询分页和 Overview 轻量响应。可按提交整体回退；
没有数据库破坏性迁移，也没有修改生产账本或真实数据。
