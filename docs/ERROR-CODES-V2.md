# v2.0 错误码契约

> 权威来源：`ab_screener/domain/errors_v2.py`（`ERROR_CODES` 注册表）。
> 机器快照：`error_codes_manifest()` → 冻结文件 `docs/contracts/error-codes-v2.json`（P0 后续生成）。
> 规则：错误码只能从注册表选择；引用未注册码 → fail-closed（ValueError）。

## 错误码清单（P0 冻结）

| code | 默认消息 | retryable |
|---|---|---|
| VALIDATION_FAILED | 输入校验失败 | false |
| UNKNOWN_ERROR_CODE | 未知错误码 | false |
| DATA_AVAILABLE_AFTER_DECISION | 该数据在决策时尚不可用 | false |
| SNAPSHOT_DECISION_CONFLICT | 快照与决策时点冲突 | false |
| MISSING_DECISION_AT | 缺少 decision_at 查询时点 | false |
| IDEMPOTENCY_KEY_REUSED | 幂等键已被不同请求使用 | false |
| IDEMPOTENCY_KEY_MISSING | 写操作缺少 Idempotency-Key | false |
| NOT_FOUND | 资源不存在 | false |
| CONFLICT | 资源冲突 | false |
| ALREADY_EXISTS | 资源已存在 | false |
| FEATURE_DISABLED | 该功能当前未启用 | false |
| RESEARCH_MODE_NOT_FULL | 研究模式非 full，禁止 edge 话术 | false |
| EVIDENCE_INSUFFICIENT | 证据不足 | false |
| CONSTRAINT_VIOLATION | 组合约束违规 | false |
| LIVE_TRADING_ENABLED | 实盘开关被打开，启动必须失败 | false |
| DB_QUICK_CHECK_FAILED | 数据库完整性检查失败 | true |
| SCHEMA_INCOMPATIBLE | 数据库 schema 与代码不兼容，请先迁移 | false |
| MIGRATION_REQUIRED | 存在未应用的迁移 | false |
| SCHEDULER_BUSY | 调度器忙 | true |
| BACKUP_STALE | 备份过期 | false |
| INTERNAL_ERROR | 内部错误 | true |

## 扩展规则

- 新增错误码：在本文件与 `errors_v2.ERROR_CODES` 同步登记；`test_error_code_registry.py`
  的 manifest 测试会校验注册表完整性与序列化。
- envelope 结构（`V2Error.to_envelope`）：`code/message/details/retryable/request_id`，
  与纸面既有错误格式兼容（`{"detail": {...}}` 由 HTTP 层包装）。
