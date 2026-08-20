# accumulation_breakout 六阶段治理与最终验收计划

## 1. 目标与当前基线

目标仓库固定为 `E:\CODEX\Stock_selection\accumulation_breakout`，不修改 D 盘平台。保留模块化单体架构，将 Web API 与扫描计算拆成独立进程，但不拆微服务。

当前验收基线：

- Python 测试：152 项通过，92 条第三方弃用警告。
- 本地日线：403 个交易日，研究模式为 `degraded`，不得宣称已验证 edge。
- 37 条 API 全部集中在 `web/backend_app.py`。
- 市场数据同时存在 SQLite 与日期级 pickle 两条读取路径。
- Tushare 已统一从 `tushare_init.py` 初始化；Token 继续只放 `.env`/环境变量，指定网关保持不变。
- 纸面交易模块及现有 API 必须全量回归，真实交易始终关闭。

最终结构采用：

- `ab_screener/domain`：信号、扫描、成本、配置和错误模型。
- `ab_screener/data`：SQLite repository、数据版本、Parquet 派生缓存。
- `ab_screener/research`：回测、基线、样本外验证和晋级门禁。
- `ab_screener/application`：扫描编排、查询服务和持久任务。
- `ab_screener/api`：FastAPI routers、契约类型。
- `ab_screener/jobs`：独立扫描 Worker、心跳、恢复和取消。
- 原入口保留为兼容壳；`backend_app.py` 和 `run_screener.py` 分别控制在约 200 行以内。

## 2. 核心架构、数据与公共接口

### 数据单一事实源

- SQLite 是唯一业务数据源；旧 `.pkl` 文件不再被读取，也不自动删除。
- Parquet 仅是可删除、可重建的派生缓存。缓存失效或损坏时回退 SQLite，不得回退 pickle。
- 按交易日维护分区版本：数据集、交易日、行数、内容 SHA-256、revision、ingested_at。
- Parquet key 由日期范围、列集合、代码集合和所含分区哈希共同生成；写入采用临时文件加原子改名。
- 扫描查询只加载所需日期、列和代码，禁止无条件读取并排序全表。

新增仅追加 SQLite 表：

- `dataset_partitions`：数据分区版本和内容指纹。
- `scan_jobs`：持久任务、状态、取消标记、心跳、重试和错误码。
- `scan_runs`：as_of、策略快照、Git SHA、数据版本、随机种子、输入/结果哈希。
- `scan_run_candidates`：预筛、严格检测、A/B 池各阶段结果和淘汰原因。
- `strategy_profiles`：类型化配置、schema version、版本、状态和配置哈希。
- `research_runs`：IS/OOS、基线、成本、扰动和晋级结论。

### 扫描进程模型

- API 只创建任务、查询状态和请求取消，不执行重计算。
- 一个独立 Worker 顺序领取扫描任务，并独占一个最大 `min(cpu-1, 8)` 的进程池。
- 子进程只接收代码列表、Parquet 路径、配置哈希和日期，不传递全量 DataFrame。
- 任务状态固定为 `QUEUED/RUNNING/CANCELLING/CANCELLED/SUCCEEDED/FAILED`。
- Worker 每 250ms 检查取消状态；检测循环至少每处理一个标的检查一次。
- 取消只终止当前 Worker 已登记的池进程，禁止使用 `os.kill(ppid, 0)` 或结束未知父进程。
- Worker 崩溃后，带完整检查点的任务重新排队；已固化成功结果的任务不得重复写入。
- 启动器作为 supervisor 同时管理后端和 Worker，并记录 PID、启动时间、命令行、工作目录和实例 ID。发现非本项目占用 8000 时只报告，不强杀。

日常采用：

- 每天：全市场向量化轻量预筛，再对入围及观察池运行完整检测。
- 每周：执行不走增量捷径的全市场完整复核，比较漏检率。
- 历史验证中 A 池信号不得出现预筛假阴性；出现即阻断该预筛版本发布。

### 配置和回测口径

- 将散落常量收口到严格校验的 `StrategyProfile`；默认配置使用版本化 JSON。
- 原 `config.py` 只做兼容导出，不再保存业务规则真源。
- 扫描和研究结果均保存完整 canonical JSON、schema version 和 SHA-256。
- 策略实验不能直接改变 A 池；只有通过晋级门禁的 profile 才能成为后续生产扫描配置。

研究回测固定口径：

- 每笔名义资金 100,000 元，数量按 100 股向下取整。
- 收盘信号只能在下一可交易日开盘尝试成交。
- 双边佣金万三、每边最低 5 元；卖出印花税千一；双边其他费万一；双边滑点万十。
- 停牌、无量、一字涨停买入、一字跌停卖出均零成交。
- 滑点后价格限制在当日高低价范围内。
- 同一日止损和目标同时触发时按止损优先的保守口径。
- 同时报告毛收益、成本、净收益和未成交机会。
- 随机基线使用固定种子 `20260808`、相同样本数和持有期；技术基线使用 20/60 日均线交叉，并走相同成交和成本引擎。

### API 与前端契约

全部现有 URL 和核心字段保持兼容，新增字段为可选字段。重点调整：

- `/api/scan` 返回持久 `task_id`，并补充 `run_id/config_hash/as_of/dataset_version`。
- `/api/scan/status` 和取消接口改为读取持久任务，不再依赖内存字典。
- 新增 `GET /api/scan/runs`、`GET /api/scan/runs/{run_id}`，支持扫描回放和漏斗。
- `/api/overview` 只返回摘要、排名和信号解释摘要，不运行检测或加载完整 K 线。
- 股票详情接口继续按标的按需返回 K 线、资金流和规则明细。
- Lab 接口返回明确的 `research_mode/can_claim_edge/gross/net/baselines/promotion_checks`。
- `/api/health` 增加构建版本、启动时间、实例 ID、Worker 心跳、数据库 schema 和数据版本。

TypeScript 为账户、扫描任务、候选、研究运行、订单和对账建立显式类型及状态联合类型。`Record<string, unknown>` 仅允许出现在原始 JSON 适配边界，不得作为页面业务状态或表格行模型。

## 3. 六阶段实施顺序

### 阶段 1：可信度止血与历史扩容

- 备份 SQLite、`portfolio.json` 和当前构建产物。
- 通过现有断点续传同步将本地有效历史扩至目标 730 日，最低验收为 720 个完整交易日。
- 校验最新交易日、主键、OHLC、成交量、来源和可用时点。
- `degraded` 模式下统一禁止“已验证 edge”“可下单参数”等表述。
- 先补扫描运行元数据和研究快照，保证后续重构前后的结果可比较。

阶段门禁：`research_status` 为 `full`，或系统继续明确 fail-closed，不允许以降级结果晋级。

### 阶段 2：领域包、SQLite Repository 与 Parquet

- 建立 `ab_screener` 包和类型化领域对象。
- 把行情、扫描结果、策略参数查询迁入 repository。
- 引入分区指纹和 Parquet 派生缓存。
- 旧入口调用新服务；旧 pickle 读取路径完全关闭。
- 对同一输入执行旧、新扫描影子比较，不对外双写两套业务结果。

阶段门禁：缓存命中与直接 SQLite 读取生成相同输入哈希和候选结果；修改任一分区后相关缓存必定失效。

### 阶段 3：持久任务、独立 Worker 与扫描性能

- 增加持久任务表、Worker 心跳、检查点和崩溃恢复。
- 将线程、扫描子进程和内部进程池收敛为 supervisor + API + 单 Worker + 单进程池。
- 向量化预筛，避免重复 `sort_values`；进程间只传轻量参数。
- 实现每日两段扫描和每周全量漏检审计。
- 修复残留 PID、版本不一致和 Access Denied 处理路径。

阶段门禁：重复领取不重复写结果；取消在 3 秒内完成；API 进程不因扫描或取消退出。

### 阶段 4：路由拆分、配置档案与可信回测

- 按 system、overview、scan、stock、lab、paper 拆分 Router。
- 将大文件收缩为 app factory/CLI 兼容入口。
- 引入类型化策略档案、配置快照和晋级状态机。
- 实现固定名义资金的成本、滑点和不可成交模型。
- 增加随机及均线基线、IS/OOS、参数扰动和净收益报告。
- 只有 `full` 模式且现有 OOS/WF/成本门禁全部通过时才允许 profile 晋级。

阶段门禁：37 条原 API 契约全部兼容；相同数据、代码、配置和种子产生完全一致的结果哈希。

### 阶段 5：可信研究终端前端

- Dashboard 增加环境、数据新鲜度、扫描元数据、候选漏斗和 A/B 池解释。
- 股票详情增加结论、规则检查清单、信号时间线和数据时间戳。
- Lab 明确标注“研究实验，非下单”，展示配置快照、IS/OOS、毛净差异、基线和晋级阻断原因。
- 将 StrategyLab 拆为页面编排、类型化 hooks 和独立组件。
- ECharts 改为模块化按需导入，仅在图表路由加载。
- 保持纸面交易页面明确标注“不会向券商下单”。

阶段门禁：桌面和窄屏均可完成扫描、查看详情、研究比较和纸面交易主要流程；键盘操作与焦点可见。

### 阶段 6：全链路验收、发布与观察

- 运行完整 Python、前端、API 契约、性能和真实数据门禁。
- 对固定历史日期执行旧、新引擎结果对比并解释所有差异。
- 使用完整历史执行成本回测、基线和晋级报告。
- 先启用新数据层，再启用 Worker，最后启用新前端；每步均可独立关闭。
- 连续观察至少五个交易日，检查任务恢复、数据新鲜度、候选数量、漏检审计和纸面日结。

## 4. 测试与最终验收

必须新增并通过：

- 数据：SQLite/Parquet 等价、分区变更失效、损坏缓存重建、禁止读取 pickle。
- 时点：`available_at > decision_at` 的数据不可进入信号、回测或晋级。
- 任务：并发领取、重复提交、Worker 崩溃恢复、取消、服务重启和幂等固化。
- 进程：取消扫描不会结束 pytest、API、supervisor 或未知 PID。
- 回测：次日开盘、整手、最低佣金、税费、滑点、停牌、涨跌停、双触发和零成交。
- 研究：403 日继续 fail-closed；720+ 日才可进入 full；降级运行不得写 active profile。
- API：现有 37 条路径、状态码和核心字段契约快照保持兼容。
- 前端：类型检查、页面状态、键盘操作、错误反馈和 Lab 隔离。
- 回归：纸面账户、导入、订单、撮合、日结、对账和真实数据门禁不得退化。

统一质量门禁：

```text
python -m pytest
python -m ruff check .
python -m mypy ab_screener paper_trading web/backend_app.py
npm --prefix web/frontend run build
python -m paper_trading.real_data_gate --days 730 --report runtime/gates/
```

性能口径固定在当前 Windows 6 逻辑核、16GB 环境：

- 160 日全市场完整扫描：Parquet 热缓存不超过 120 秒。
- SQLite 冷加载并构建缓存：不超过 180 秒。
- 扫描期间 Worker 及子进程合计峰值 RSS 不超过 4GB。
- 取消请求到任务终态不超过 3 秒。
- `/api/overview` 热请求不超过 500ms，响应小于 300KB。
- 100 个持仓、1,000 个订单时纸面账户摘要不超过 500ms。
- 每周全扫与日常预筛相比，A 池假阴性必须为零。
- 前端首屏不加载 ECharts；单个构建资源不超过 800KB 原始体积。

最终完成条件：

- 本地历史达到 full 门槛，真实数据门禁使用有效环境凭据通过。
- SQLite 成为唯一事实源，pickle 不再参与运行。
- 扫描可持久、可恢复、可取消且不影响 API。
- 回测不存在同收盘信号同收盘成交路径，全部成本可逐项复算。
- 扫描、研究和策略晋级均可由配置、数据、代码和结果哈希复现。
- 全部质量门禁通过，现有 152 项测试及新增测试全部通过。
- 没有真实券商连接或下单能力，`LIVE_TRADING_ENABLED=false`。

## 5. 发布、回滚与固定假设

- 数据库迁移仅新增表、列和索引，不删除旧数据。
- 发布前保留数据库、旧构建和 `portfolio.json` 的带时间戳备份。
- 回滚开关固定为 `SCANNER_ENGINE=legacy|v2`、`MARKET_CACHE_MODE=off|parquet`、`SCAN_WORKER_ENABLED=false|true`。
- 回滚到旧扫描器时强制关闭缓存，禁止重新启用可能过期的 pickle。
- `scan_result` 保留为最新扫描结果的兼容投影，新审计数据写入新增表。
- Tushare 调用继续统一引用现有初始化文件；真实 Token 不进入源码、测试、报告或日志。
- ETF 仅在具备独立 instrument 规则和真实数据门禁后宣称支持；本轮主要验收对象仍是现有 A 股扫描标的。
- 周报推送、消息通知、PDF 导出和跨仓迁移不纳入本轮完成条件。
