# 阶段1 验收报告：数据库迁移与领域模型

日期：2026-08-07
状态：✅ 全部通过

## 交付内容

### 1. 增量迁移机制（`paper_trading/migrations.py`）
- `schema_version` 版本表（version/name/checksum/applied_at，checksum=迁移函数源码 sha1 防篡改）
- 有序迁移列表 MIGRATIONS + 执行器 `run_migrations()`：空库全跑 / 已有库只跑缺失 / 重复执行 no-op
- 每个迁移独立 BEGIN IMMEDIATE 事务；迁移函数内 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 检列，双重幂等
- 挂接 `LocalStore.__init__` 末尾，backend/sync/测试自动迁移
- 只新增表/列，绝不 DROP/修改原表结构与数据

### 2. 领域表（`paper_trading/schema.py`，14 张表）
账户 / 信号快照 / 订单 / 成交 / 现金流水 / 持仓批次 / 日结快照 / 交易日循环 / 对账 / 公司行为 / 审计事件 / 门禁报告 + trade_cal + instrument_rules
- 金额存分（整数）、价格存微元（整数）、数量整数股、时间 ISO8601+08:00
- 字段级 CHECK 防浮点（`amount_fen = CAST(amount_fen AS INTEGER)`）/防负值；REFERENCES 防未知规则
- `pt_order` 完整状态机 CHECK + `idempotency_key UNIQUE`（并发预留单次成功前提）

### 3. BEGIN IMMEDIATE 事务（`paper_trading/db.py` + local_store `_connect(immediate=True)`）
- `tx()` 用 isolation_level=None 显式事务 + BEGIN IMMEDIATE 拿写锁
- 现有 `_connect()` 加 `immediate=False` 默认参数，向后兼容零影响

### 4. daily 行情元数据列（M001）
- 新增 available_at / ingested_at / source / revision / is_legacy 5 列
- 存量 218 万行按交易日分批（402 批）标记 `legacy_backfill`/`is_legacy=1`，available_at=下一交易日 09:30+08:00
- sync_from_tushare 新数据填真实抓取完成时间 + source='tushare'/revision=1

### 5. 交易日历 + 交易规则（`cal.py` / `rules.py`）
- trade_cal：Tushare 优先落库，异常/空 → 本地推断（周末+内置法定节假日 2024~2027）；`is_open/next_open/prev_open`
- instrument_rules：默认股票佣金5bp/最低5元/卖出税10bp/其他1bp/滑点10bp；ETF 税0/滑点5bp；`get_rule` 自动落库；未知类型抛 DomainError

### 6. 领域错误（`errors.py`）
`DomainError(code, message, details, retryable)` + 常用错误码常量

## 验收结果

| 验收项 | 判定规则 | 实测 | 结果 |
|---|---|---|---|
| 空库迁移 | schema_version 全版本 + 14 表齐 | 4 版本全跑, 14 表 | ✅ |
| 已有库副本迁移 | 只补缺失版本, 原数据保留 | 补齐 4 版本, 行数不变 | ✅ |
| 重复迁移幂等 | 二次执行 no-op | 0 版本, schema_version 行数不变 | ✅ |
| 迁移不破坏原表 | daily/scan_result 行数与采样值逐列一致 | 2,185,517 行不变, 旧列值一致 | ✅ |
| 非法订单状态被拒 | CHECK 拒绝 BOGUS_STATE | IntegrityError | ✅ |
| 浮点金额被拒 | CHECK(amount=CAST) 拒绝 1.5 | IntegrityError | ✅ |
| 未知交易规则被拒 | 未知 inst_type/负佣金/零手数 | IntegrityError | ✅ |
| 并发确认单次预留 | 8 线程同 key → 恰 1 成功 | 恰 1 条, 库内 1 条 | ✅ |
| 现网规模迁移 | 938MB 副本 | 22.2s, 218 万行标记, 原数据完整 | ✅ |

**测试**：新增 5 个测试文件 23 项测试（test_migrations 4 / test_migration_preserves 2 / test_domain_constraints 7 / test_cal_rules 6 / test_concurrent_reserve 2）+ 现有 81 项全量回归。

## 文件清单

**新增**：
- `paper_trading/__init__.py`、`schema.py`、`migrations.py`、`db.py`、`cal.py`、`rules.py`、`errors.py`
- `tests/test_migrations.py`、`tests/test_migration_preserves.py`、`tests/test_domain_constraints.py`、`tests/test_cal_rules.py`、`tests/test_concurrent_reserve.py`

**修改**：
- `local_store.py` — `_connect(immediate=False)`、`__init__` 挂接迁移、`upsert_daily` 元数据列、`sync_from_tushare` 填元数据+落库 trade_cal

**不修改**：8 张原表结构与数据、API 路由、portfolio.py、前端。

## 备注
- 真实库（runtime/stock_data.db）本次**未迁移**——当前后端进程持有它，将在阶段 3 激活账本时或下次重启时自动完成迁移（幂等安全）。
- 迁移对 938MB 库实测 22.2s 一次性完成，重复执行 0.2s no-op。
- 校验和（checksum）绑定迁移函数源码，改动已发布迁移会因版本跳过而不会被重放（防篡改）。
