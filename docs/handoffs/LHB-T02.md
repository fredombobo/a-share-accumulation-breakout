# LHB-T02 Handoff — 数据源适配、增量抓取和原始快照

> 自报完成不等于验收通过。

## 1. 身份

- 任务 ID：T02
- Agent 角色：实现 Agent（Wave B，与 T04 同会话顺序交付）
- 基线 commit：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`（`closers-g2-split`）
- 交付 commit：无（未要求提交）
- 时间（Asia/Shanghai）：2026-08-29

## 2. 范围核对

实际修改 / 新增：

- 新增 `ab_screener/data/adapters/lhb_sources.py`
- 新增 `ab_screener/application/lhb_ingest.py`
- 修改 `ab_screener/data/adapters/tushare_pit.py`（`top_inst` / `hm_list`、业务键补齐、合并 LHB PIT 表）
- 新增 `tests/test_lhb_source_adapters.py`
- 新增 `tests/fixtures/lhb/source_status.json`

未修改但工作区已脏：`docs/INDEX.md`、closers/NTM 文档、`web/frontend/dist/**`。本任务未触碰。

## 3. 设计

- Tushare 只经注入 fake `pro` 或根 `tushare_init`；无裸 requests。
- 空结果默认 `NOT_PUBLISHED`；仅 `published=True` 才标 `VALID_EMPTY`。超时 / 限流 / 缺字段 / HTML 变化 → `FETCH_FAILED`，不写业务行。
- 官方源未注入获准客户端时 fail-closed（`OFFICIAL_FETCH_NOT_AUTHORIZED`），不绕过验证码或反爬。
- 主源失败 + 备用源有行 → `DEGRADED`，不得标 `COMPLETE`。
- 重试上限 3，熔断默认 5 次失败。幂等键 = dataset + partition + source + status + content hash + error_reason。
- 交易日历补洞用 `missing_trade_dates`，不是 MAX(date)。

未改 feature flag、A 池、订单、`LIVE_TRADING_ENABLED`。

## 4. TDD 证据

```powershell
.\.venv312\Scripts\python.exe -m pytest tests\test_lhb_source_adapters.py tests\test_lhb_contracts.py tests\test_lhb_migrations.py tests\test_lhb_normalization.py -q
# 40 passed
```

## 5. 质量证据

- 定向 Pytest：含 T02 在内 40 passed（全 LHB 测试文件）
- Ruff（本波修改文件）：pass
- Mypy（lhb_sources / tushare_pit / lhb_ingest）：pass
- 前端 / OpenAPI：未运行（不改 UI）
- 真实网络 / Token / 生产库：用户显式提供 Token 后，仅经 `tushare_init` 跑 smoke；未写生产库
- 真实接口 smoke：已运行。证据 `runtime/lhb_smoke_last.json`
  - `http_url=http://a.sszhixia.cn/`
  - `trade_date=20260803`
  - `top_list` 56 行，字段含 `reason/amount/net_amount`
  - `top_inst` 580 行，字段含 `exalter/side/buy/sell/net_buy/reason`
  - `hm_list` 113 行，字段 `name/desc/orgs`
  - Token 未写入该 JSON（`token_preview=REDACTED`）

## 6. 回滚

删除新增 adapter/ingest/测试；还原 `tushare_pit.py`。未写生产库。

## 7. 自评

- 建议判定：待验收（真实 smoke 已补）
- 后续：T03 已在同会话推进
- 未宣称可实盘或保证收益

## 8. 管理者复验

- 最终判定：**返工复验通过**。
- 已关闭：v1 必需字段与金额有限性校验 fail-closed；默认使用真实、可注入的指数退避；缺字段、NaN、Infinity、负数和默认退避反例均通过。
- 下一步：T06 可继续；完整证据见 `docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`。
