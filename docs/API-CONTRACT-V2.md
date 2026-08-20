# v2.0 API 契约（冻结初版）

> 机器 schema 生成后本契约升级为 `docs/contracts/openapi-v2.yaml`；本文件为 P0 冻结的
> 行为契约。前端类型从 schema 生成或由字段级 contract test 校验。

## 1. 基础

| 项 | 契约 |
|---|---|
| API 前缀 | 新能力 `/api/v2`；现有 `/api/paper` 保持兼容 |
| 时间 | ISO 8601 `+08:00`；交易时区 Asia/Shanghai |
| 金额 | API Decimal 字符串；账本整数分/定点价格 |
| 分页 | 列表默认 50、最大 100，稳定 cursor（`cursor` + `next_cursor`） |
| 版本头 | 响应含 `X-AB-Version`（build_version）；请求可选 `X-AB-Entry-Def` |

## 2. 查询时点（PIT 语义）

- 情报/策略解释/比较/风险历史查询必须传 `snapshot_id` 或 `decision_at`；
  默认只允许服务端最新「完成且数据门禁通过」快照，响应必须显式返回它。
- 冲突 → HTTP 409 `SNAPSHOT_DECISION_CONFLICT`。

## 3. 写操作

所有 side-effect POST/PUT/PATCH/DELETE：

- 强制 `Idempotency-Key`（缺 → 400 `IDEMPOTENCY_KEY_MISSING`）；
- 服务端保存 method/path/actor/request canonical hash/response/status；
- 同 key + 同 hash → 原结果；同 key + 异 hash → 409 `IDEMPOTENCY_KEY_REUSED`；
- 必须写 audit event；
- 纯只读 POST（压力计算）在 OpenAPI 标 `x-side-effects: false`。

## 4. 错误 envelope

```json
{"code": "SCHEMA_INCOMPATIBLE", "message": "数据库 schema 与代码不兼容，请先迁移",
 "details": {}, "retryable": false, "request_id": "..."}
```

- 前端默认显示「原因+解决方式」，技术 details 折叠；
- 错误码只能来自 `ab_screener/domain/errors_v2.py` 注册表（见 ERROR-CODES-V2.md）。

## 5. 版本化 feature flags（configs/platform_v2.yaml）

| flag | 开发默认 | 说明 |
|---|---:|---|
| V2_PIT_READ_ENABLED | false | PIT 双写验证后切正式读 |
| V2_EXECUTION_DUAL_RUN_ENABLED | true | 只比较旧/新执行核心 |
| V2_EXECUTION_WRITE_ENABLED | false | parity 通过后允许新核心写账 |
| V2_STRATEGY_REGISTRY_ENABLED | false | 六形态插件 registry |
| V2_RISK_ENFORCEMENT_ENABLED | false | observe→enforce |
| DAILY_SCHEDULER_ENABLED | false | 自动 EOD DAG |
| INSTITUTIONAL_CONSOLE_V2_ENABLED | false | v2 控制台 |
| LIVE_TRADING_ENABLED | false | 硬断言：true 启动失败 |

flag 不能关闭：资金、份额、T+1、不做空、数据时点、对账、实盘隔离硬门。
所有 flag 进入 resolved config hash（`ab_screener/application/platform_config.py`）。
