# V2R-[任务号] Agent 交付模板

> 本文件由实现 Agent 填写。自报完成不等于验收通过；管理者复验后才会更新任务板。

## 1. 身份

- 任务 ID：
- Agent 角色：
- 基线 commit：必须是任务板指定 base，或管理者书面批准的新 integration commit
- 分支：
- worktree 绝对路径：
- 交付 commit：
- 开始/完成时间（Asia/Shanghai）：

## 2. 范围核对

- 实际修改文件：
- 是否全部位于 owned_paths：是/否
- 是否触碰 protected/shared paths：是/否
- 若是，附管理者批准记录：
- 未解决的工作区变更：

必须附原始输出：

~~~powershell
git status --short
git diff --stat <base_commit>..<delivery_commit>
git diff --name-only <base_commit>..<delivery_commit>
~~~

## 3. 根因与设计

- 原始失败或缺口：
- 根因：
- 采用方案：
- 未采用方案及原因：
- 是否改变 API、表结构、配置、策略语义、成交语义或风险语义：

涉及交易/研究时必须回答：

- decision_at 与 available_at 如何保证：
- 是否存在同收盘信号同收盘成交路径：
- 金额是否保持整数分/定点价格：
- 是否改变 A/B 池资格或订单生成：
- LIVE_TRADING_ENABLED 是否仍为 false：

## 4. TDD 证据

### 失败测试

- 测试名称：
- 修改前命令：
- 修改前预期与实际：

### 最小实现

- 关键实现文件和入口：
- 幂等策略：
- 失败模式：
- 日志/审计：

### 通过测试

逐条粘贴命令与摘要，不写“测试已通过”这种无证据描述。

~~~powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest <精确测试> -q
~~~

## 5. 质量证据

- 定向 Pytest：
- Ruff（限制到修改文件）：
- Mypy（限制到修改模块）：
- OpenAPI/契约测试：
- 前端 build/test/E2E：
- 性能数字：
- 数据库副本/fixture：

如果某项未运行，必须写“未运行”及原因，不能写 N/A。

## 6. 数据与运行证据

- 数据库路径：生产库/副本，必须明确
- 数据日期：
- 数据库 fingerprint：
- 代码 SHA：
- config hash：
- 产物路径：
- 产物 SHA-256：
- 是否访问外部数据源：
- 是否包含 Token/账户号：必须为否

## 7. 回滚

- 回滚 commit：
- 配置回滚：
- 数据回滚或冲正：
- 是否需要停止服务：
- 是否存在不可逆操作：

禁止把删除账本、删除失败审计、覆盖生产数据库写作回滚方法。

## 8. Agent 自评

- 建议管理者判定：待验收 / 已知阻断
- 已知缺陷：
- 后续依赖：
- 明确声明：本 Agent 未宣布 PERSONAL_INSTITUTIONAL_READY。

## 9. 管理者区（实现 Agent 不填）

- 范围审查：
- 代码审查：
- 定向复验：
- 交叉域复验：
- 运行态复验：
- 判定：ACCEPTED / REWORK_REQUIRED / REJECTED
- 缺陷编号：
- 允许进入的下一任务：
