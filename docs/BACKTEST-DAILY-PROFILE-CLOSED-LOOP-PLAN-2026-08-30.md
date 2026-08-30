# 回测参数与今日选股闭环实施计划

日期：2026-08-30  
产品：`accumulation_breakout` / AB-Screener / 8001

## 目标

把专业回测中经过样本外、滚动窗口、基线和成本压力检验的参数，保存为可追溯的“每日扫描参数档案”。用户必须人工启用，今日选股随后冻结并使用同一版技术入场参数；可随时恢复系统默认。

本功能解决参数口径漂移，但不把“样本内最佳”或一次回测等同为已证明的生产优势。

## 闭环

1. 专业回测继续使用与扫描共享的 `detect_accumulation_breakout` 信号函数。
2. 完成回测后检查 OOS、WF、基线和 2 倍成本结论，以及代码、数据身份是否仍有效。
3. 只有 `EXPLORATORY_PROMISING` 且身份未过期的结果允许人工启用；弱结论和过期结果明确阻断。
4. 启用时保存不可变参数快照、来源任务、数据/代码版本、证据摘要和配置哈希。
5. 每次今日扫描开始时解析一次有效档案并冻结到扫描任务；子进程、结果和审计都使用该快照，不在运行中重新读取。
6. 恢复系统默认只停用自定义档案，不删除历史记录和扫描证据。

## 一致参数与产品边界

共享技术入场参数：

- 横盘最短/最长天数、最大振幅；
- 突破量比、突破涨幅上下限；
- 相对近期量能比例、突破确认窗口、结构条件；
- 止损、量能退出、强势重置和退出窗口作为回测/操作参考。

今日选股仍额外执行数据新鲜度、市场环境、资金流、基本面、流动性和评分门禁。这些是生产候选过滤，不属于回测技术入场参数；页面必须明确披露，因此“参数一致”不承诺回测交易与每日 A 池逐只完全相同。B 观察池继续使用固定宽松规则，不冒充启用档案的 A 池结果。

## 影响文件

- `ab_screener/domain/profile.py`
- `ab_screener/data/strategy_profile_repository.py`
- `ab_screener/application/strategy_profile_service.py`
- `ab_screener/screener/evaluator.py`
- `ab_screener/screener/orchestrator.py`
- `ab_screener/application/scan_spawn.py`
- `ab_screener/application/scan_job_runner.py`
- `ab_screener/api/routers/legacy_scan.py`
- `ab_screener/api/routers/professional_backtest.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/pages/Overview.tsx`
- `web/frontend/src/pages/ProfessionalBacktest.tsx`
- `web/frontend/src/pages/Guide.tsx`
- 相关 Python、Vitest 和 Playwright 测试

## 验收

- 默认状态明确显示“系统默认参数”，且保持当前扫描行为兼容。
- 弱结论、未完成任务、代码版本或数据版本不一致均不能启用，并给出人话原因。
- 合格结果经人工确认后可幂等启用；同一任务重复请求不生成不同版本。
- 今日扫描响应、子进程结果和 `scan_runs.strategy_snapshot_json` 的参数与配置哈希完全一致。
- 扫描运行期间切换档案不影响已启动任务，下一任务才使用新档案。
- A 池严格检测使用启用档案的全部共享入场参数；B 池仍明确为固定宽松观察规则。
- 恢复默认不删除历史档案或旧扫描审计。
- 页面展示当前版本、来源回测、关键参数、适用边界及恢复入口。
- 严格 Pytest、Ruff、Mypy、架构检查、前端测试、生产构建及真实 8001 浏览器验收通过。
- `LIVE_TRADING_ENABLED=false`，不新增纸面或真实交易能力。

## 回滚

调用恢复默认接口即可立即让后续扫描回到内置参数。代码回滚无需删除 `strategy_profiles` 记录；历史参数与扫描审计继续只读保留。
