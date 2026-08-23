# v2.0 纠错工程管理者验收规程

## 1. 管理原则

本文件只供管理者执行验收。实现 Agent 的测试摘要、handoff 或“已完成”不能替代独立复验。

每个交付只能得到三种任务级结论：

- ACCEPTED：范围、实现、定向测试、交叉测试和回滚均满足。
- REWORK_REQUIRED：方向可保留，但存在明确可修缺陷。
- REJECTED：越权、破坏硬门、伪造证据、范围失控或方案与合同冲突。

任一以下行为直接 REJECTED：

- 打开 LIVE_TRADING_ENABLED；
- 将 Token、账户号或个人信息提交/打印；
- 删除失败测试或扩大 ignore/exclude 制造绿灯；
- 为通过研究门修改门槛、删失败窗口或只保留最佳运行；
- 使用未来 available_at 数据；
- 同收盘信号同收盘成交；
- 使用浮点数记现金或费用；
- 覆盖生产数据库、账本、审计、备份或 portfolio.json；
- 未经批准修改 protected/shared paths；
- 自行宣布 PERSONAL_INSTITUTIONAL_READY。

## 2. 单任务验收

### 2.1 身份和范围

- [ ] base commit 与任务板一致。
- [ ] 每个交付有唯一 head commit。
- [ ] git status 干净，或明确列出未纳入交付的用户文件。
- [ ] changed files 全部位于 owned_paths。
- [ ] 未混入 web/frontend/dist、runtime DB、Token 或无关重构。
- [ ] handoff 使用统一模板且字段齐全。

### 2.2 代码审查

- [ ] 正常路径和失败路径各有测试。
- [ ] 公共接口有类型标注。
- [ ] 领域失败使用结构化错误。
- [ ] 写操作有幂等和审计。
- [ ] 数据读取满足 available_at <= decision_at。
- [ ] 金额为整数分/定点价格。
- [ ] 研究/生产共享 ENTRY 和执行定义。
- [ ] 回滚不删除不可变业务记录。

### 2.3 复验

管理者不复用 Agent 的 pytest 缓存：

~~~powershell
$py = "E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe"
& $py -m pytest <任务定向测试> -q -p no:cacheprovider
& $py -m ruff check <任务修改的 Python 文件>
& $py -m mypy <任务修改的公共模块>
~~~

- [ ] 修改前失败证据与根因一致。
- [ ] 修改后定向测试真实收集并通过。
- [ ] 与相邻域的契约测试通过。
- [ ] 性能指标来自 wall-clock，不是估计。
- [ ] 数据测试使用 fixture 或明确副本。

### 2.4 任务判定记录

管理者在对应 handoff 的“管理者区”填写：

- 范围审查；
- 关键代码审查；
- 复验命令与结果；
- 缺陷编号和级别；
- ACCEPTED / REWORK_REQUIRED / REJECTED；
- 下一任务是否解锁。

## 3. Wave 1 出口

V2R-Q1：

- [ ] today API 测试隔离稳定。
- [ ] baseline identity 测试不再扫描 16GB 生产库。
- [ ] 没有放宽生产 baseline 深检。

V2R-A：

- [ ] 固定 scanner golden 逐字段不变。
- [ ] ENTRY、评分阈值和 A/B 池语义未改。
- [ ] Windows spawn、取消和进度写回归通过。
- [ ] 根兼容 shim 保留，内核按职责拆分。

V2R-D：

- [ ] 公司行为 PIT 数据具备五元组。
- [ ] 副本 backfill 可恢复且幂等。
- [ ] 无 Token/无权限非零退出。
- [ ] PIT shadow parity 有可复算报告。
- [ ] PIT 正式读仍关闭。

V2R-X：

- [ ] 研究/纸面 dual-run 零分差。
- [ ] review/confirm 共享风险核心。
- [ ] observe 不改变订单，enforce 测试可阻断。
- [ ] 执行写和风险 enforce 默认仍关闭。

V2R-F：

- [ ] Vitest 和 Playwright 实际存在并运行。
- [ ] Monitor/Review/System/Compare 页面具备完整状态。
- [ ] 390px、键盘、恢复 E2E 通过。
- [ ] 未提交 dist，前端不计算账务。

V2R-O1：

- [ ] system health 热请求目标低于 500ms。
- [ ] GET 不执行完整 integrity_check。
- [ ] AB_BACKUP_ROOT 未配置时明确失败。
- [ ] strict DryRun 在 headless PowerShell exit 0。
- [ ] 没有删除既有备份。

只有六项全部 ACCEPTED，管理者才创建 Wave 2 integration commit 和精确 worktree。

## 4. Wave 2 出口

V2R-S：

- [ ] 扫描重放不产生重复 observation。
- [ ] EXPERIMENTAL 不进入 A 池。
- [ ] ENTERED 只由 fill 触发。
- [ ] outcome 时点和 UNFILLABLE 语义正确。

V2R-O2：

- [ ] DAG 顺序与合同一致。
- [ ] dag_runs、step_runs、lease、audit 在副本有真实记录。
- [ ] 重放不重复成交/现金/持仓/信号。
- [ ] fault_injection marker 有测试且全绿。
- [ ] 五日 soak 已启动；未满时 O 保持 BLOCKED。

V2R-R：

- [ ] 600 股、步长 5、完整网格。
- [ ] 净成本 IS/OOS、双基线、三个 WF。
- [ ] PBO/DSR/MinTRL/容量齐全。
- [ ] 报告身份和 SHA-256 完整。
- [ ] FAIL/INSUFFICIENT 被如实保留。

V2R-N：

- [ ] 信息记录有完整 PIT 五元组。
- [ ] 无权限返回 INSUFFICIENT。
- [ ] 开关覆盖层前后 A/B、仓位和订单逐项一致。
- [ ] 不伪造供应商能力。

## 5. Wave 3 出口

V2R-Q2：

- [ ] Ruff 0 error。
- [ ] Mypy 0 error。
- [ ] Pytest 0 failed。
- [ ] performance 和 fault_injection 均收集到测试并通过。
- [ ] 前端 test/build 通过。

V2R-G：

- [ ] v2 flags 有服务端消费者。
- [ ] readiness 有生产 API 调用点。
- [ ] dirty/identity mismatch 强制 BLOCKED。
- [ ] 仅 R 失败才允许 ENGINEERING_READY_RESEARCH_BLOCKED。
- [ ] 全 PASS 状态名为 PERSONAL_INSTITUTIONAL_READY。
- [ ] v2 导航不允许本地越权。
- [ ] 最终 dist 一次构建，运行 build 与本地一致。
- [ ] LIVE_TRADING_ENABLED=false。

## 6. P8 七闸门

| 门 | PASS 的最小证据 |
|---|---|
| D | 当前身份、24h 内真实数据门禁；PIT/公司行为/持仓覆盖满足 |
| R | 完整研究报告；可为 FAIL，但不能缺证据 |
| S | 生产信号生命周期实际记录、幂等、插件晋级纪律 |
| P | 风险快照、约束手算、压力测试和确认路径接线 |
| L | 订单→成交→现金/持仓→日结→对账可重复且零差异 |
| O | DAG、审计链、7 份备份、严格恢复、五真实交易日 soak |
| G | 干净身份、质量门、API/UI/E2E、flags/readiness、安全 |

合法最终状态：

- 任一硬门未过：BLOCKED。
- 仅 R 未过且其余门全 PASS：ENGINEERING_READY_RESEARCH_BLOCKED。
- 七门全 PASS：PERSONAL_INSTITUTIONAL_READY。

无论哪种状态，LIVE_TRADING_ENABLED 都必须保持 false。

## 7. 纠错循环

管理者发现缺陷时：

1. 在 handoff 管理者区写缺陷 ID，例如 V2R-X-RW-001。
2. 标明证据、影响文件、期望行为和精确复验命令。
3. 任务板状态改为 rework_required。
4. 原 Agent 在原分支追加修复 commit，不 force-push、不重写已审 commit。
5. 管理者只复验新增 diff 加原失败用例。
6. 接受后更新 accepted_commit；未接受不得释放依赖任务。

## 8. 官方台账更新

只有管理者在 P8 阶段执行：

- 调和 tasks/backlog.yaml 与 tasks/implementation_state.yaml；
- 更新过时的 AGENTS.md、docs/STATUS.md 和 docs/RESEARCH-ROADMAP.md；
- 保留用户原有修改，不整文件覆盖；
- 将旧证据标记 superseded，不删除；
- 写 docs/ACCEPTANCE-V2-REMEDIATION-FINAL.md；
- 记录代码 SHA、配置 hash、DB fingerprint、报告 SHA-256 和回滚方式。
