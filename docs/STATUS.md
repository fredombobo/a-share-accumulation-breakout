# 项目状态看板

> 更新：2026-08-11 · Windows 扫描进度竞态修复完成

## 当前结论

系统已具备本地 A 股扫描、可信研究、纸面交易、日结与对账的完整功能链。九分闭环已经通过；Windows 下后端读取进度文件与子进程原子替换之间的短暂共享冲突也已修复，并完成全量测试、实际扫描和新构建真实数据门禁。

研究结论继续保持 fail-closed：600 只完整实验报告为 `FAIL`；没有同时通过净成本 OOS、三窗 WF、随机/MA20-60 双基线和反过拟合门禁前，不登记候选参数，更不会进入 A 池或生成订单。

## 五条闭环状态

| 闭环 | 当前状态 | 权威证据 |
|---|---|---|
| 实验生命周期 | 已通过 | SQLite `research_runs`；单活动唯一索引；持久取消/恢复 |
| 每日证据链 | 已通过 | `daily_run_manifests`；20260807 为 `COMPLETE` |
| 研究候选门禁 | 已通过并正确阻断 | `personal-anti-overfit-v1`；600 只报告为 FAIL |
| 今日唯一下一步 | 已通过 | `GET /api/today`；总览只显示一个主动作 |
| 发布证据 | 已通过 | `GET /api/release/readiness`；当前构建真实数据门禁 PASS |

## Windows 扫描竞态验收

- 根因：后端每 0.35 秒读取进度 JSON 时，Windows 读句柄短暂阻止子进程 `os.replace` 覆盖同一路径。
- 修复：仅对 Windows 共享冲突 `WinError 5/32` 执行有限退避重试；真实权限错误继续抛出。
- 状态收口：子进程错误现在同步把 `scan_jobs` 从 `RUNNING` 落为 `FAILED`。
- 遗留任务：`f4039c03cfdc` 保留原错误证据并更正为 `FAILED`。
- 真实扫描：`5a8751c10328` 在 1,050 次额外读锁干扰下完成，API 为 `done`、数据库为 `SUCCEEDED`，扫描审计已固化且无 `.tmp` 残留。
- 当前门禁：构建 `ecc00348f332`；968 个交易日，20×5 源端抽样零差异；报告 SHA-256 `0567c64f4e96e81b0ad110e37c5ae299504df1b9acd2883a07bc64a8bd01337f`。

## 已验证质量

- 当前 Pytest：350 passed；本轮扫描相关测试：23 passed。
- Ruff：全仓通过。
- Mypy：51 个源文件通过。
- 前端：TypeScript + Vite 构建通过；ECharts 大块仅为性能警告。
- 真实运行：实验并发请求返回 409；显式取消落到持久化 `cancelled`；真实数据门禁曾覆盖 968 个交易日且 100 组源端抽样零差异。

## 每日使用

1. 打开总览，只执行“今天建议做什么”卡片中的唯一主动作。
2. 扫描完成后查看 A 池；策略实验室只用于验证，不是下单入口。
3. 纸面订单必须人工确认，且只在下一可交易日开盘仿真撮合。
4. 日结后查看对账与当日日清单；存在阻断差异时不得继续生成买入草稿。
5. 发布前单独运行真实数据门禁，再检查 `/api/release/readiness`。

## 安全边界

- `LIVE_TRADING_ENABLED=false`，没有券商适配器，不会产生真实订单。
- 交易域金额使用整数分/定点价格；纸面账本不可通过浮点前端重算。
- 研究 PASS 仅代表“隔离候选”，不自动进入扫描或纸面交易。
- `.env` 与 Token 不入库、不进报告、不写日志。

## 文档索引

- [小白使用手册.md](./小白使用手册.md)
- [操作手册.md](./操作手册.md)
- [RESEARCH-ROADMAP.md](./RESEARCH-ROADMAP.md)
- [2026-08-11 九分闭环实施计划](./superpowers/plans/2026-08-11-nine-point-closed-loop.md)
- [九分闭环验收记录](./NINE-POINT-CLOSED-LOOP-ACCEPTANCE-2026-08-11.md)
- [机构级控制台升级计划（待执行）](./superpowers/plans/2026-08-11-institutional-console-upgrade.md)
