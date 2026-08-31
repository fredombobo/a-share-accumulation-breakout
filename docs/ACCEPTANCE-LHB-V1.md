# 龙虎榜 V1 验收报告（2026-08-29）

> 系统定位：个人研究 overlay。本文件记录工程交付与研究门禁，**不是**实盘许可。
> 工程 PASS ≠ 研究 edge PASS。禁止把本报告读成“可跟单”或“已验证赚钱”。

## 结论摘要

| 维度 | 状态 |
|---|---|
| 本地研究产品 | **READY（仅隔离副本、盘后研究）** |
| T01–T12 工程实现 | 已交付并完成真实链路验收 |
| 清单第 6 节「工程通过」全条件 | **BLOCKED（官方跨源核验 + 仓库既有 Ruff 债务）** |
| 研究 / edge | **RESEARCH_BLOCKED** |
| 进入 A 池 / 生成订单 | 否 |
| `LIVE_TRADING_ENABLED` | false（硬门） |
| `V2_PIT_READ_ENABLED` | false |
| `DAILY_SCHEDULER_ENABLED` | false |

## 产品验收证据

### 真实数据与数据库隔离

- Tushare 真实 smoke（20260828）：`top_list=57`、`top_inst=630`、`hm_list=113`；调用只经过根适配器，日志未输出 Token。
- 真实副本连续 5 个交易日（20260824–20260828）均为 `COMPLETE`；所有 15 个 dataset/day manifest 均为 `COMPLETE`。
- 标准事实：300 个事件、2,103 条席位交易、2,586 条买卖排名、3,163 个席位主数据、4,677 个画像快照。
- 7 次真实执行记录（含两日复跑）全部 `COMPLETED`；94 个 step attempt 全部 `SUCCESS`，证明复跑可审计。
- 产品副本：`runtime/lhb_product.db`，16,518,692,864 bytes，最新行情日 20260828；`v2:lhb_tracking`、`v2:lhb_ops` 已应用且 schema compatible。
- 生产库：`runtime/stock_data.db`，最新行情日同为 20260828，但没有任何 `lhb*`、`seat*`、`actor*` 表，确认本次没有对生产库迁移或写入。

### API、仪表盘与浏览器

- 正式 dist 的 Playwright 端到端通过：雷达 API/UI 各 50 条，网络 184 个节点、978 条边，个股时间线 4 条，画像与回测页可见。
- 全部相关 HTTP 响应为 200，浏览器控制台错误为 0。
- 修复了正式构建中 v2 API 的双重 `/api` 前缀，以及 UTC `Z` 查询时间与 `+08:00` PIT 字符串比较不一致的问题。
- 截图：`runtime/lhb_browser_e2e.png`。

### 质量门

- 龙虎榜专项：`145 passed in 28.74s`。
- 全量离线回归：`806 passed, 2 warnings in 163.99s`；两条告警为 sklearn 版本提示和 Windows GBK 子线程解码提示，不是测试失败。
- Ruff（本轮触及的产品代码）：pass；仓库级 `ruff check .` 仍有 114 条既有/范围外债务，未批量改写用户的其他模块。
- 关键模块 mypy：8 source files 无问题；`scripts/check_architecture.py --strict`：pass。
- 前端：`tsc -b && vite build` 成功；dist 只由正式构建生成。

### 安全与研究边界

- 历史晚到回填写入 0 个 signal observation，这是防止 hindsight/PIT 泄漏的预期行为，不是流水线遗漏。
- 68 条告警投递全部为 `CREATED + dry_run=1`，没有伪报 ACK，也没有真实外发。
- 下一开盘收益模型已执行 A 股 T+1，并对停牌、一字跌停延迟退出。
- overlay 仍为研究解释层，不改变 A/B 池分数、仓位或订单。

## 本轮主要修复

- 迁移注册表兼容已退役 migration id 与历史完整 SHA-256 checksum，消除全量 collection 崩溃。
- 后端和 legacy 路由尊重 `AB_DB_PATH`；启动时对实际运行库 fail-closed 校验，迁移脚本拒绝生产库。
- API 的席位、主体类型、置信度、`as_of` 与分页过滤真实生效；网络和回测摘要不再返回占位数据。
- 产品 EOD 流水线接入真实抓取、raw/manifest、标准化、映射、画像、研究信号、报告与 dry-run 告警。
- 产品启动器直接服务隔离副本的正式 dist，不再复用可能指向其他数据库的 8001 进程。

## 仍阻断正式晋升的硬门

1. **官方跨源核验未获授权**：当前报告明确记录 `official_reconciliation=NOT_AUTHORIZED`，真实 `lhb_reconciliation` 为 0。合成对账测试通过，但不能替代交易所授权数据证据。因此不在清单第 6 节写“工程通过”。
2. **研究 edge 未证明**：`research_status=RESEARCH_BLOCKED`、`can_claim_edge=false`。
3. **Shadow maturity 未达到**：尚无 3 个月且不少于 30 个成熟独立信号；建议 6–12 个月/100 个独立事件后再讨论晋升。
4. **自动调度未启用**：已提供可运行的盘后命令，但 `DAILY_SCHEDULER_ENABLED=false`。需要先完成正式门禁和单独调度验收。
5. **真实通知未配置**：当前只有 dry-run `CREATED` 记录，明确属于未送达、未 ACK。
6. **仓库级 Ruff 基线未清零**：全量 pytest 与产品路径 Ruff 均通过，但全仓 Ruff 仍报告 114 条存量问题；这不阻断本地研究产品运行，但按清单第 6 节不能宣告全仓工程通过。

## 产品入口与证据路径

- 运行手册：`docs/LHB-PRODUCT-RUNBOOK.md`
- 产品启动：`scripts/start_lhb_product.ps1`
- 每日流水线：`scripts/run_lhb_eod.py`
- 副本准备：`scripts/prepare_lhb_product_db.py`
- Handoff：`docs/handoffs/LHB-T01.md` … `LHB-T12.md`
- 实施清单：`docs/LHB-TRACKING-IMPLEMENTATION-CHECKLIST.md`
- 定向测试：`scripts/run_lhb_pytest.ps1`

## 明确禁止的表述

- 已验证 edge、确定性赚钱、可下单参数、实盘跟单或保证收益。
- “工程测试全绿，所以策略已经可上实盘”。
