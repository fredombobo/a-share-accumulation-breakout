# 手工研究参数与大网格回测实施计划

## 目标

将 AB-Screener 明确收敛为个人研究学习平台，同时保留三种彼此独立、来源可追溯的今日扫描参数来源：系统默认、用户手工输入、专业回测结果。用户手工参数无需先通过回测；专业回测结果仍只有满足既有探索证据门槛时才能人工启用，两条路径不得互相冒充。

专业回测有效组合硬上限由 512 提升为 5,120。超过 512 组不再拒绝，但预览必须标记为长耗时任务，启动前要求用户显式确认；超过 5,120 组继续 fail-closed。新增清晰的止损与止盈百分比参数，二者必须进入真实的 T+1 回测退出模拟、参数哈希、排行榜、报告和每日扫描风险参考。

## 边界

- 产品文案使用“研究候选、研究参数、学习验证”，不使用“荐股”或“机构推荐”承诺。
- 手工参数只改变下一次 A 池技术扫描与风险参考；B 池及数据新鲜度、市场环境、资金流、基本面、流动性和评分门禁不变。
- 手工参数会以不可变版本写入现有 `strategy_profiles`，扫描启动时继续冻结完整快照和哈希。
- 止盈在买入日之后才允许触发；同一交易日同时触及止损和止盈时按保守顺序先止损，禁止未来函数。
- 不恢复纸面交易，不连接券商，`LIVE_TRADING_ENABLED=false`。
- 不修改受保护的 `docs/STATUS.md` 与 `docs/RESEARCH-ROADMAP.md`。

## 影响文件

- `ab_screener/domain/profile.py`
- `ab_screener/data/strategy_profile_repository.py`
- `ab_screener/application/strategy_profile_service.py`
- `ab_screener/research/professional_grid.py`
- `ab_screener/research/professional_runner.py`
- `ab_screener/research/backtest_engine.py`
- `ab_screener/api/routers/professional_backtest.py`
- `optimizer.py`
- `trade_sim.py`
- `trade_plan.py`
- `ab_screener/screener/orchestrator.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/pages/Overview.tsx`
- `web/frontend/src/pages/ProfessionalBacktest.tsx`
- `web/frontend/src/pages/Guide.tsx`
- `web/frontend/src/styles/theme.css`
- `docs/小白使用手册.md`
- 相关 Python、Vitest 与 Playwright 测试

## 实施步骤

1. 扩展版本化参数契约，加入 `target_pct`，并为旧档案提供向后兼容的哈希读取。
2. 新增手工研究参数激活服务和 API；复用专业参数目录的类型、范围和跨字段校验。
3. 将止盈参数接入 bench 出场回放，保持 T+1、止损优先、成本和组合会计链路不变。
4. 将网格硬上限提升到 5,120，并增加 512 组长任务提示阈值及预览元数据。
5. 首页增加手工参数表单和来源标签；专业回测增加醒目的百分比止损/止盈区与长任务确认。
6. 全站关键文案调整为研究学习定位，补充说明书和使用手册。
7. 运行定向测试、严格质量门、生产构建和 8001 浏览器验收；生产数据库仅做只读核对，不替用户改变当前档案。

## 验收标准

- 用户可直接在首页填写并确认手工参数；无需回测任务，保存后下一次扫描冻结相同参数和哈希。
- 手工档案明确标记“未经过回测验证”，不改变正式研究晋级结果；恢复默认仍可用且历史不删除。
- 参数缺失、未知、越界或跨字段冲突均返回结构化错误，数据库不产生半成品。
- 止损与止盈以百分比明显展示；`target_pct` 参与参数空间、参数 ID、IS/OOS/WF、成本压力、排行榜和报告。
- 止盈只从买入后的下一交易日起检查；同日同时触及止损/止盈时止损优先；固定历史输入重复运行结果一致。
- 513 至 5,120 组预览成功并返回长耗时警告，前端启动前二次确认；5,121 组及以上拒绝。
- 默认页面不再将 A 池描述为机构荐股或买入指令，始终显示研究学习和不连接券商边界。
- Python 单元/集成/契约测试、Vitest、Playwright、Ruff、Mypy、严格架构检查和前端构建全部通过。
- 8001 身份仍为 accumulation_breakout，生产当前档案未被验收过程改写，`LIVE_TRADING_ENABLED=false`。
