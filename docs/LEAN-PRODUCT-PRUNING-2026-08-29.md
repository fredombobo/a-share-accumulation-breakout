# accumulation_breakout 精简产品收口

## 目标

把 8001 收敛为本机个人每天真正使用的横盘吸筹突破闭环：更新行情、扫描、查看候选、查看个股证据、纸面仿真。没有成熟证据或不服务日常决策的功能退出发布产品。

## 保留的用户功能

1. `/`：唯一日常首页，包含数据状态、扫描、A/B 池、资金热力图和板块资金流。
2. `/stock/:tsCode`：个股 K 线、箱体、资金流、基本面和交易计划证据。
3. `/paper`：纸面账户、订单预览、仿真成交、持仓和对账；永不连接券商。

## 退出发布产品

- `/lab`、`/backtest`。
- 全部 `/v2/*` 控制台页面：指挥舱、情报、六形态、信号、研究治理、监控、复核、系统和对比。
- 生产 Web 不再挂载实验室、交互回测和 logic platform 路由。
- 生产 Web 只挂载 v2 的平台就绪度、纸面只读状态与系统审计底座；其余机构控制台路由不挂载。
- 顶栏停止实验任务轮询；侧栏不再显示实验/机构入口。

## 明确保留的后台底座

- PIT 数据、交易日历、公司行为和防未来函数检查。
- 研究与回测引擎、不可变历史报告及正式晋级门；仅通过离线命令运行。
- Decimal 撮合、现金/持仓账本、风险约束、对账。
- 审计链、真实数据门禁、备份恢复与 readiness。

以上模块即使不显示在日用界面，也承担正确性、安全和复现职责，不属于可删除的 UI 膨胀。

## 验收标准

- 发布前端只有 `/`、`/stock/:tsCode`、`/paper` 三类路由；旧链接自动返回首页。
- 侧栏只有“每日选股”和“纸面仿真”两个入口，个股详情由候选卡或代码搜索进入。
- 不再轮询 `/api/lab/status`，构建产物不包含 Lab、BacktestStudio 或 v2 页面 chunk。
- 8001 OpenAPI 不包含 `/api/lab*`、`/api/backtest*`、`/api/logic*` 及机构控制台业务路径。
- 数据同步、扫描、总览、个股详情、纸面交易、平台状态和审计安全接口保持可用。
- `LIVE_TRADING_ENABLED=false`；账本、历史报告和运行证据零删除。
- 相关契约测试、Ruff、Mypy、Pytest、Vitest 和生产构建通过。

## 回滚

本次变更只修改代码装配、导航和 feature flag，不迁移或删除数据库。需要恢复时回退对应提交并重启 8001；历史数据与研究产物仍在。

## 实施与验收结果

- 发布提交：`89d2a6b`（`refactor(product): ship lean breakout workflow`）。
- 前端由 14 个路由收敛为 3 类路由，侧栏由 13 项收敛为 2 项；删除 4,821 行发布面代码。
- 生产构建由 22 个 JavaScript 文件收敛为 5 个；不再产生 Lab、BacktestStudio 或 v2 页面 chunk。
- 8001 OpenAPI 由 102 条路径收敛为 47 条；`/api/lab*`、`/api/backtest*`、`/api/logic*`
  及 desk/intelligence/research/review/signals/strategies 等机构业务路径均为 0。
- 核心 `/api/health`、`/api/scan`、`/api/paper/dashboard`、订单和对账接口仍在；生产数据库未迁移、未删表。
- 完整质量门通过：Ruff、Mypy、架构检查、960 项 Pytest、4 项 Vitest 与前端生产构建全部通过。
- 真实 8001 浏览器验收通过：主页和纸面页可用；旧 `/lab`、`/backtest`、`/v2/monitor`
  自动回首页；控制台错误、页面异常、失败请求均为 0。截图：`runtime/lean-ui-acceptance.png`。
- 运行身份为 `accumulation_breakout`，数据日 `20260828`，行情新鲜，`LIVE_TRADING_ENABLED=false`。

本任务只判定“精简产品发布面”完成，不改变机构七闸门结论。研究仍为 FAIL/NO_CANDIDATE，
本次代码身份变化后 readiness 也会在证据重建前保持 `BLOCKED/IDENTITY_MISMATCH`；不得据此宣称
`PERSONAL_INSTITUTIONAL_READY`。
