# V2 Remediation Wave 1 最终验收

- 日期：2026-08-23（Asia/Shanghai）
- 管理基线：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`
- 集成分支：`v2r-wave1-integration`
- 业务集成代码提交：`5cf7088`
- Wave 1 判定：`ACCEPTED`
- 全系统判定：`BLOCKED`（Wave 2/3、真实数据与真实时间证据尚未完成）

## 1. 合并范围

| 任务 | 接受的代码提交 | 管理复核提交 | 最终判定 |
|---|---:|---:|---|
| V2R-Q1 | `8310b08` | `4e01fb0` | ACCEPTED |
| V2R-A | `26f77eb` | `53a796a` | ACCEPTED |
| V2R-D | `cab2afe` | `1294a28` | ACCEPTED |
| V2R-X | `57ddea4` | `532b03b` | ACCEPTED |
| V2R-F | `17cb5a3` | `bcbe7ed` | ACCEPTED |
| V2R-O1 | `b953773` | `bdc4ef4` | ACCEPTED |

六个分支以 `--no-ff` 合并到独立干净 worktree；主工作区原有未提交内容未参与合并，也未被覆盖。

## 2. 管理者直接纠错

### 2.1 前端门禁可执行性

- 重新执行 `npm ci`，定位到旧 `node_modules` 中 `http-proxy-agent` 文件残缺，而非 lockfile 缺项。
- 修正 Vitest 查询歧义和健康接口 mock，使测试验证真实字段而非松散文本。
- Playwright 服务固定绑定 `127.0.0.1`，消除 Windows `localhost -> ::1` 差异。
- E2E mock 仅拦截 URL path 以 `/api/` 开头的后端请求，避免把 Vite 的 `/src/api/*.ts` 模块误返回 JSON；同时增加页面标题、真实输入框、390px 与键盘焦点断言。

### 2.2 schema 检查保持只读

集成测试发现 `schema_compatible()` 使用只读 SQLite 连接，却在迁移登记表缺失时调用 `CREATE TABLE IF NOT EXISTS`。已改为只读查询 `sqlite_master`；旧库返回逐项 `MIGRATION_PENDING`，Web 启动仍 fail-closed，但不会在兼容性检查中写库。新增回归测试验证数据库字节和表集合均不变化。

### 2.3 全量测试脱离生产库和目录名

三条全量测试原先隐含依赖“主仓库真实数据库非空”和目录名必须为 `accumulation_breakout`，在合法 worktree 中失败。现改为确定性临时 SQLite 行情 fixture，并根据模块文件位置验证项目根；业务实现未放宽，生产数据库未读取或写入。

### 2.4 合并静态债务

删除 3 个分支遗留的未使用测试导入并整理 import block；未修改策略、费用、风险、PIT 或成交语义。

## 3. 验证证据

| 门禁 | 结果 |
|---|---|
| Wave 1 定向/交叉 Pytest | `137 passed, 1 skipped` |
| schema registry 回归 | `6 passed` |
| worktree 隔离失败集 | `18 passed` |
| 合并测试清理复验 | `21 passed` |
| Ruff（全部 Wave 1 变更 Python 文件） | PASS |
| Mypy（24 个变更生产模块） | PASS |
| Vitest | `6 passed` |
| TypeScript + Vite build | PASS |
| Playwright Chromium | `4 passed` |
| 全量 Pytest | `748 passed, 1 skipped`，0 failed，188.13s |

预期跳过项只允许带明确原因；不得把失败改成 skip。生成的 `runtime/` 验收库和 baseline manifest 均被 `.gitignore` 排除，仅用于该 worktree，未触碰生产库。

## 4. 安全与发布边界

解析 `configs/platform_v2.yaml`（空环境 overlay）得到以下值，均保持关闭：

- `LIVE_TRADING_ENABLED=false`
- `V2_PIT_READ_ENABLED=false`
- `V2_EXECUTION_WRITE_ENABLED=false`
- `V2_RISK_ENFORCEMENT_ENABLED=false`
- `DAILY_SCHEDULER_ENABLED=false`

resolved config hash：`08177cf56042f116`。

`npm audit` 仍报告 5 项开发/测试依赖漏洞：Vitest critical、Vite high、`@vitest/mocker`/esbuild/vite-node moderate。功能验收可接受，但发布门不得通过；禁止执行破坏性 `npm audit fix --force`，应在后续质量任务中显式升级并复跑 Vitest/Build/Playwright。

全量测试另有两个非阻断警告需进入 Q2：Windows 子进程输出按 GBK 解码时出现线程级 `UnicodeDecodeError`；pandas 对含全空列的 concat 行为发出 future warning。它们没有造成测试失败，但在最终发布质量门前应被显式消除。

## 5. Wave 1 任务结论

### V2R-D

三个返工项已关闭：公司行为真实同步、当前持仓/活动订单/A 池优先抽样、独立真实数据门禁接线均有定向测试；PIT 正式读开关保持关闭。

### V2R-X

两个返工项已关闭：risk enforce 读取 resolved flag；风险入口异常在 observe/enforce 两态均返回结构化降级或 fail-closed。费用 round-half-up 与 dual-run 零分差路径通过。

### V2R-F

四个返工项已关闭：lockfile 与安装可执行、Vitest 真执行、固定 E2E 端口、真实浏览器流程可观测。`dist` 不进入功能提交。

### V2R-O1

三个返工项已关闭：DryRun 自动推导安全临时恢复目标、HTTP 不可覆盖备份根、快速健康通过 SQL trace 证明不执行全库 integrity/quick check。调度开关保持关闭。

## 6. 仍然阻断全系统就绪的事项

Wave 1 接受不代表 `PERSONAL_INSTITUTIONAL_READY`。至少仍需：

1. V2R-S 完成不可变信号到 fill/outcome 的生产闭环；
2. V2R-R 以 full 数据完成固定 600 股、步长 5 的净成本 IS/OOS/WF 与反过拟合报告；
3. V2R-N 完成 PIT-safe 只读信息覆盖层且证明不影响 A/B 池、仓位或订单；
4. V2R-S 接受后才能释放 V2R-O2，完成持久 EOD DAG、故障恢复和真实五交易日 soak；
5. 清除 npm audit 与全仓质量债务，统一构建和七闸门复验；
6. 真实数据、研究、备份和 soak 证据必须与同一代码/配置/数据库身份匹配。

## 7. 下一波释放

- 立即并行：`V2R-S`、`V2R-R`、`V2R-N`。
- 暂不启动：`V2R-O2`；它依赖 `V2R-S`，必须等 S 管理验收后再创建任务工作树。
- 所有 Agent 必须从管理者最终发布的 Wave 2 精确 base SHA 开始，不得从旧 `b6772c3` 或各自 Wave 1 分支继续。
