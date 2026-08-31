# LHB-T01 Handoff — 冻结数据契约、迁移和状态模型

> 自报完成不等于验收通过。主验收人应按本文件核验证据，而不是只看结论。

## 1. 身份

- 任务 ID：T01
- Agent 角色：实现 Agent（Wave A）
- 基线 commit：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`（`closers-g2-split`）
- 分支：`closers-g2-split`
- 工作树：`E:\CODEX\Stock_selection\accumulation_breakout`
- 交付 commit：无（未要求提交）
- 开始/完成时间（Asia/Shanghai）：2026-08-29

## 2. 范围核对

实际修改 / 新增文件：

- 新增 `ab_screener/domain/lhb_contracts.py`
- 新增 `ab_screener/data/migration_intents/lhb_tracking_v2.py`
- 修改 `ab_screener/data/migration_intents/__init__.py`（注册 `lhb_tracking_v2`）
- 新增 `docs/DATA-DICTIONARY-LHB-V1.md`
- 新增 `tests/test_lhb_contracts.py`
- 新增 `tests/test_lhb_migrations.py`
- 新增 `docs/handoffs/LHB-T01.md`
- 更新 `docs/LHB-TRACKING-IMPLEMENTATION-CHECKLIST.md`（T01 勾选）

未修改但工作区已脏（本任务未触碰）：

- `docs/INDEX.md`
- `docs/superpowers/plans/2026-08-21-institutional-closers-index.md`
- `web/frontend/dist/**`（hash 资源，禁止手工改）
- 若干既有 untracked closers / NTM 文档

## 3. 根因与设计

- 原始缺口：仓库只有 `top_list_history` 汇总 PIT，没有席位级事实表、身份假设、金额/排名分离和抓取状态契约。
- 采用方案：新增独立迁移 `v2:lhb_tracking`（依赖 `v2:pit_history` + `v2:aux_history`），不改已发布 intent。领域层冻结状态枚举、事件键、来源单位→元→分换算和身份语言。
- 未采用：改 `v2:aux_history` 塞 `top_inst`（checksum 会漂）；金额用 float 元（无法精确到分）。
- API / 成交语义 / A 池 / 订单：未改。
- `LIVE_TRADING_ENABLED`：仍为 false。`lhb_signal_observation.research_only` 有 `CHECK (research_only = 1)`。
- `decision_at` / `available_at`：契约强制 `+08:00`；身份映射按 `valid_from/valid_to` + revision 追加，T05 才能按事件日读取。

## 4. TDD 证据

命令与摘要（退出码 0）：

```powershell
E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe -m pytest tests\test_lhb_contracts.py tests\test_lhb_migrations.py -q --tb=short
# 21 passed in 3.41s
```

覆盖：空库双跑、已有 v2 副本双跑且 `top_list_history` 行数不变、已发布 11 个 migration checksum 冻结、append-only 触发器、同股同日多原因 + D1/D3 唯一键、双榜金额一行排名两行、非法日期/负金额/净额不一致/未知状态/重复键、research_only 不能为 0。

## 5. 质量证据

- 定向 Pytest：21 passed
- 相关回归：`tests/test_migration_registry_v2.py tests/test_pit_repository.py tests/test_platform_config.py` → 19 passed
- Ruff（修改文件）：All checks passed
- Mypy：`ab_screener/domain/lhb_contracts.py` + `ab_screener/data/migration_intents/lhb_tracking_v2.py` → Success
- OpenAPI / 前端：未运行（T01 不改 API/UI）
- 数据库：仅 pytest 临时库；未打开 `runtime/stock_data.db`
- 外部网络 / Token：未使用

## 6. 数据与运行证据

- 生产库：未写入
- 是否包含 Token/账户号：否
- feature flag：未改 `configs/platform_v2.yaml`
- 金额：领域口径元，存储分；Tushare `top_list/top_inst` 龙虎榜金额字段的来源口径均逐字段冻结为元
- 已发布 checksum 冻结值见 `tests/test_lhb_migrations.py::PUBLISHED_MIGRATION_CHECKSUMS`

## 7. 回滚

- 代码回滚：删除本任务新增文件，并还原 `__init__.py` 中 `lhb_tracking_v2` 导入。
- 数据：未对生产库执行迁移。若已在副本 `--apply`，副本丢弃即可；禁止 DELETE 账本行充当回滚。
- 不可逆操作：无
- 无需停服务

## 8. Agent 自评

- 建议管理者判定：待验收
- 已知限制：
  - `top_inst_history` 尚未并入 `pit_writer.ALL_HISTORY_TABLES`（T02）
  - 原因文本 → `reason_code` 映射引擎未实现（T04）
  - 官方源 adapter 未实现；T02 必须 fail-closed，不得绕过反爬
- 后续依赖：Wave B = T02（抓取）与 T04（标准化），二者都依赖本契约
- 明确声明：未宣布 PERSONAL_INSTITUTIONAL_READY；未宣称可实盘跟单或保证收益。

## 9. 管理者区（实现 Agent 不填）

- 最终判定：**返工复验通过**。Tushare `top_list/top_inst` 金额白名单已冻结为元，真实量级回归通过。
- 允许进入的下一任务：T06。
- 验收证据：`docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`
