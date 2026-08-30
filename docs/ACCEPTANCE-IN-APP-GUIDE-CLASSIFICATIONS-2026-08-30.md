# 站内说明书与多标准分类验收

日期：2026-08-30  
产品：`accumulation_breakout` / AB-Screener / 8001  
实现提交：`9e718c434bef`  
结论：**PASS**

## 1. 验收范围

- 在发布产品内增加详细使用说明书，不恢复旧实验室、纸面交易或机构控制台。
- 首页资金热力图和板块资金流支持细分行业、上市板块、地域三种真实分类。
- 专业回测使用相同分类定义，可搜索、多选并冻结分类、分组、代码集合与哈希。
- 保留旧 `industries` 请求兼容；未知分类与未知分组失败关闭。

## 2. 页面与使用逻辑

新增 `/guide` 页面和侧栏“使用说明”帮助入口。页面包含 8 个章节：

1. 每天怎么用；
2. 系统怎么选股；
3. A 池和 B 池；
4. 板块分类标准；
5. 专业回测；
6. 如何阅读结果；
7. 进度与异常；
8. 系统边界。

说明书直接读取 `/api/classifications`，因此分类名称、覆盖率、分组数量和示例来自当前本地数据，不在前端伪造。

## 3. 分类数据验收

生产数据库：`E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db`。只读检查结果：

| 分类 | 当前非空快照分组 | 专业回测有效分组 | 说明 |
|---|---:|---:|---|
| 细分行业 | 110 | 111 | 专业回测额外明确保留“未分类”组 |
| 上市板块 | 4 | 3 | 资金面含北交所；首版专业回测仍只纳入现有沪深扫描标的 |
| 地域 | 32 | 33 | 专业回测额外明确保留“未分类”组 |

专业回测三种分类均冻结 5,216 只当前有效沪深股票的代码集合。分类来自当前 `stock_basic` 快照，响应固定披露 `CURRENT_SNAPSHOT_ONLY` 和 `CURRENT_CLASSIFICATION_FROZEN_UNIVERSE`。

申万、中信、同花顺概念及其历史成员没有本地 PIT 数据，不对外宣称支持。只有补齐历史成员、`available_at`、版本和防未来函数测试后才能成为正式回测分类。

## 4. 接口验收

- `GET /api/classifications`：PASS，返回三种分类、覆盖率、示例和时点限制。
- `GET /api/money-heatmap?classification=...`：三种分类均 PASS，继续按流入和流出每方向各取 Top 10。
- `GET /api/sector-flow?classification=...`：三种分类均 PASS。
- `GET /api/backtest/universe?classification=...`：三种分类均 PASS。
- OpenAPI 包含 `/api/classifications`：PASS。
- `classification=unknown`：返回 422、`UNKNOWN_CLASSIFICATION`、`retryable=false`：PASS。
- 旧 `industries` 字段按细分行业兼容：自动化测试 PASS。

## 5. 自动化证据

- 严格质量门：PASS。
- Ruff：PASS。
- Mypy：25 个配置源文件无问题。
- 严格架构检查：PASS。
- Pytest：`974 passed in 192.36s`。
- 前端 Vitest：4 个文件、10 个测试 PASS。
- 前端正式构建：PASS；`dist/index.html` SHA-256 为 `c59b50e1b046a4f0028a57ea868bf0c6bcdca70750ab94e882eba3e8c7167755`。
- Playwright 发布流程：8 个场景 PASS，包括分类切换与冻结、说明书、键盘和 390px。

## 6. 真实 8001 验收

- 服务来自 `v2r-final-integration`，启动构建 `4787fee42898`。
- 行情日期 `20260828`，`LIVE_TRADING_ENABLED=false`。
- `/guide` 状态 200；实时分类卡 3 张、内容章节 8 个。
- 首页切换到“上市板块”后，热力图标题和聚合结果同步变化。
- 专业回测切换到“上市板块”后显示主板、创业板、科创板。
- 桌面与 390px 页面均无横向溢出。
- 浏览器控制台错误 0，页面脚本异常 0。
- 本次真实验收只调用读接口，没有启动扫描、回测或写入业务表。

验收截图保存在本机运行目录：

- `runtime/acceptance-guide-desktop.png`
- `runtime/acceptance-guide-mobile.png`

## 7. 边界与回滚

本次通过是产品功能验收，不改变既有研究 FAIL/NO_CANDIDATE，也不改变机构/实盘总门禁的 BLOCKED。系统仍不连接券商。

回滚不涉及数据库：移除帮助路由和分类选择控件即可；API 的默认 `industry` 与旧 `industries` 兼容路径可继续保留。
