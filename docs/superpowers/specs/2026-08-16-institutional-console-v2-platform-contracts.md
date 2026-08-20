# v2.0 平台契约、风险公式与运维合同

| 字段 | 内容 |
|---|---|
| 文档 ID | `PERSONAL-INSTITUTIONAL-V2-PLATFORM-CONTRACTS` |
| 状态 | 实现合同；P0 生成机器 schema 后冻结 |
| API 前缀 | 新能力 `/api/v2`；现有 `/api/paper` 保持兼容 |
| 时间 | ISO 8601 +08:00；交易时区 Asia/Shanghai |
| 金额 | API Decimal 字符串；账本整数分/定点价格 |

## 1. Resolved config 与 feature flags

唯一 typed config：`ab_screener/application/platform_config.py`；默认文件：`configs/platform_v2.yaml`。解析顺序固定为默认文件→明确环境变量 overlay→命令行允许项，最后生成不可变 resolved config 和 SHA-256。数据库不作为第二套配置事实源；需要运行时修改时创建新 config version 并引用完整 resolved payload。

| flag | 开发默认 | 发布默认 | 说明 |
|---|---:|---:|---|
| `V2_PIT_READ_ENABLED` | false | true（P1通过后） | 切PIT正式读取 |
| `V2_EXECUTION_DUAL_RUN_ENABLED` | true | false | 只比较旧/新执行结果 |
| `V2_EXECUTION_WRITE_ENABLED` | false | true（P2通过后） | 允许新核心写纸面账本 |
| `V2_STRATEGY_REGISTRY_ENABLED` | false | true（P4通过后） | 启用插件 registry |
| `V2_RISK_ENFORCEMENT_ENABLED` | false | true（P5通过后） | observe→enforce |
| `DAILY_SCHEDULER_ENABLED` | false | true（P6通过后） | 自动EOD DAG |
| `INSTITUTIONAL_CONSOLE_V2_ENABLED` | false | true（P7通过后） | v2控制台 |
| `LIVE_TRADING_ENABLED` | false | **false硬断言** | 任何 true 启动失败 |

feature flag 不能关闭资金、份额、T+1、不做空、数据时点、对账和实盘隔离硬门。

## 2. ScanProfile 合同

```json
{
  "profile_id": "uuid",
  "profile_version": 1,
  "name": "robust-personal-default",
  "is_default": true,
  "universe": {
    "instrument_type": "A_SHARE_EQUITY",
    "include_st": false,
    "include_delisted_asof": true,
    "min_listed_trading_days": 120,
    "min_adv20_rmb": "50000000"
  },
  "scan": {"lookback_days": 160, "top_n": 20},
  "plugins": [
    {"strategy_id": "...", "version": "...", "config_hash": "...", "enabled": true}
  ],
  "execution_timing_definition_id": "NEXT_TRADABLE_OPEN_EXECUTION_V1",
  "risk_profile_id": "ROBUST_PERSONAL_RISK_V2",
  "cloned_from": null,
  "resolved_config_hash": "...",
  "created_at": "..."
}
```

- 已使用版本不可修改；编辑创建新 version。
- 同一时刻只有一个 default；并发切换在事务中完成。
- 历史 run 引用精确 profile/version/hash。
- legacy 无 profile 请求映射到只读 `legacy-default-v1`，不获得 v2 readiness。

## 3. 漏斗合同

固定 DAG 节点语义：

```text
UNIVERSE
→ DATA_ELIGIBLE
→ LIQUIDITY_ELIGIBLE
→ PLUGIN_EVALUATED:<strategy_id>
→ QUALIFIED:<strategy_id>
→ A_TRADEABLE:<strategy_id> | B_WATCHING:<strategy_id>
```

每节点保存输入/输出集合 hash、计数、排除 reason counts 和 duration。验收为每条边 `child_set ⊆ parent_set`，A/B 两分支并集不超过 QUALIFIED；禁止要求两个插件之间计数单调。

## 4. 风险 profile 与公式

`ROBUST_PERSONAL_RISK_V2`：

| 项 | 固定口径 |
|---|---|
| 单票/总仓/最低现金/日新增 | 10% / 80% / 10% / 20% |
| 行业或主题/高相关组 | 25% / 30% |
| 纸面参与率硬上限 | `min(profile=5%, instrument_rule_limit)` |
| 研究容量参考 | 2%；同时报告1/2/5/10% |
| 风险收益序列 | 每日TWR，Asia/Shanghai交易日 |
| 年化因子 | 252 |
| 无风险率 | 年化1.5%，每日 `(1+rf)^(1/252)-1`；修改需新profile |
| Sharpe | `sqrt(252) × mean(excess_daily) / sample_std(excess_daily, ddof=1)` |
| Sortino（展示） | `sqrt(252) × mean(excess) / downside_deviation`；无下行样本为INSUFFICIENT |
| VaR/CVaR | 历史法，lookback60、min40，loss=-return，linear quantile 95%；CVaR为loss≥VaR均值 |
| 相关性 | 60日Pearson、min40；绝对相关≥0.80进入相关组 |
| TWR | 外部现金流发生点切分子期间后几何链接 |
| MWR/XIRR | 仅在完整带时点外部现金流存在时计算，否则INSUFFICIENT |
| 停牌/缺价 | 使用合法最近估值并标stale；超过profile容忍期则风险快照INSUFFICIENT/阻断 |

VaR/CVaR 以正数表示潜在损失。所有公式用手算 fixture 验证 <1bp；前端不重复计算。

## 5. 信号、订单和 outcome 关联

- `signal_observation_id` 是单插件订单的强制外键。
- 多插件订单必须引用已预登记且已批准的 `composite_decision_id`。
- 人工历史练习必须 `manual_exercise=true`、`signal_observation_id=null`。
- 订单创建前 signal 必须为 `TRADEABLE`；CONFIRMED 不改变 signal 为 ENTERED；fill 才产生 ENTERED event。
- 默认 signal validity=5个交易日，按交易日历计算。
- 取消/拒单/过期保留订单事件；不改原始 observation。

Outcome 精确口径见[六形态策略目录](2026-08-16-institutional-console-v2-strategy-catalog.md)。

## 6. API 基础合同

### 6.1 查询时点

- 情报、策略解释、比较和风险历史查询必须传 `snapshot_id` 或 `decision_at`。
- 默认只允许服务端最新“完成且数据门禁通过”的 snapshot；响应必须显式返回它。
- 两者冲突返回 HTTP 409 `SNAPSHOT_DECISION_CONFLICT`。
- 时间线 `from/to` 默认1年、最大5年；列表默认50、最大100，稳定 cursor。

### 6.2 写操作

所有 side-effect POST/PUT/PATCH/DELETE：

- 强制 `Idempotency-Key`；
- 服务端保存 method/path/actor/request canonical hash/response/status；
- 同 key+同 hash 返回原结果；同 key+异 hash 返回409 `IDEMPOTENCY_KEY_REUSED`；
- 必须写 audit event；
- 纯只读 POST（压力计算）在 OpenAPI 标 `x-side-effects: false`，不进入写操作分母。

### 6.3 错误 envelope

```json
{
  "code": "DATA_AVAILABLE_AFTER_DECISION",
  "message": "该数据在决策时尚不可用",
  "details": {},
  "retryable": false,
  "request_id": "..."
}
```

前端默认显示“原因+解决方式”，技术 details 折叠。错误码只能从 registry 选择。

### 6.4 OpenAPI 冻结

P0 输出 `docs/contracts/openapi-v2.yaml` 和 `docs/contracts/error-codes-v2.json`。后端契约测试比较 checked-in schema；前端类型从 schema 生成或由字段级 contract test 校验。实现计划中的最小 endpoint 表与现有 `/api/paper` 兼容接口全部进入 schema。

## 7. 告警、审计、笔记和导出

### 7.1 告警

kind 至少包括 `DATA_STALE`、`TASK_FAILED`、`SIGNAL_EXPIRING`、`STOP_REMINDER`、`COMPANY_ACTION`、`RECONCILIATION_BLOCKER`、`CAPACITY_LIMIT`、`BACKUP_STALE`。severity=`INFO|WARNING|BLOCKING`。

去重键：`kind + subject_type + subject_id + trade_date + rule_version`。同一事件重复不新增；关闭后再次发生生成新 occurrence。已读/read-all 写审计。在线保留730日，随后可归档但不删除相关审计引用。

### 7.2 审计

每条含 actor/action/request_id/correlation_id/before/after/prev_hash/event_hash/occurred_at。调度内部写用 DAG correlation ID。每日 chain head 签名并写入 `AB_BACKUP_ROOT/audit-anchors/<date>.json`；私钥使用本机受保护存储，仓库只保存公钥/算法版本。验证器不宣称抵抗同时控制管理员、私钥和外部锚点的攻击者。

### 7.3 Idea/decision ledger

`research_note`：note_id、hypothesis_id、run/signal refs、content、tags、created_at、supersedes。`decision_log`：decision_id、candidate/plugin/version、decision=`PROMOTE|REJECT|RETIRE|HOLD`、reason_codes、evidence refs、actor/time。均只追加；修改通过 supersedes。

### 7.4 导出

CSV/JSON/Markdown 引用 snapshot/manifest/hash。CSV 对以 `= + - @` 开头的字符串加安全前缀，防公式注入。不存在/已篡改 artifact 返回结构化失败，不生成空文件。

## 8. 每日 DAG 合同

### 8.1 时间与模式

- 时区 `Asia/Shanghai`。
- 交易日16:15后检查最新完成数据，每分钟可触发检查，但同scope/日/input最多成功一次。
- 周末/节假日不创建交易 run；重启 catch-up 未完成交易日。
- `LIVE_EOD_REPLAY`：16:15后重放当日开盘成交；不声称实时。
- `HISTORICAL_REPLAY`：显式历史补跑，不能读取目标日之后数据。

### 8.2 步骤与默认超时

| step | scope | timeout | 依赖 |
|---|---|---:|---|
| calendar/lease | GLOBAL | 1m | 无 |
| sync | GLOBAL | 60m | calendar |
| PIT/data gate | GLOBAL | 10m | sync |
| instrument/company-action gate | ACCOUNT | 10m | data gate |
| queued-order open replay | ACCOUNT | 15m | action gate |
| valuation/settlement/reconciliation | ACCOUNT | 15m | open replay |
| market breadth | GLOBAL | 10m | data gate |
| close scan | PROFILE | 60m | data gate+breadth |
| signal projection | PROFILE | 10m | scan |
| outcome backfill | GLOBAL | 20m | data gate |
| alerts/next-day drafts | ACCOUNT+PROFILE | 10m | reconcile+signals+outcomes |
| daily manifest | GLOBAL | 5m | 所有适用步骤 |
| verified backup | GLOBAL | 30m | manifest |

`max_attempts=3` 含首次。唯一键=`trade_date+step_name+scope_type+scope_id+input_hash`。上游失败阻断下游；R失败只跳过自动策略草稿，不阻断卖出、日结和备份。

## 9. 迁移与大库合同

- 唯一 registry：`ab_screener/data/migration_registry.py`；唯一维护命令：`scripts/migrate_v2.py`。
- 新 `schema_migrations_v2` 使用 namespace/string ID 和 checksum，兼容既有 paper 1–8、core 9–13、logic 101+。
- Web/启动器只 `assert_schema_compatible`，required migration 未应用时明确阻断；不自动跑DDL/backfill，不 `except: pass`。
- DDL 与数据回填分开；约517万日线行按数据集/月、初始≤5万行批次，checkpoint 可恢复。
- 迁移和故障注入只对解析后的绝对副本路径；生产 DB fingerprint 前后相同。
- 迁移前需要已验证备份、维护窗口、WAL/新增表估算；可用空间至少 `2×当前DB + 预计新增数据`。

## 10. 备份与恢复合同

- `AB_BACKUP_ROOT` 必须不在活动数据库目录，优先第二卷/独立同步目录；缺失时 O 非PASS。
- SQLite online backup 写随机 tmp，完成 `integrity_check`、`foreign_key_check`、关键表行数/hash 后原子命名。
- manifest 含源DB fingerprint、backup hash、schema、created/verified time、tool version。
- 至少连续7份；删除前必须有另一份已验证备份；不得删除唯一可用副本。
- 目标 RPO≤1交易日、RTO≤30分钟；每周恢复到临时目录并实际启动只读健康检查。
- 备份含个人持仓/账务，目录权限最小化；不得提交、上传公共位置或写日志内容。

## 11. Evidence canonicalization

- canonical JSON v1：UTF-8、对象键Unicode字典序、无空白、Decimal为规范字符串、时间ISO+08:00、数组保持业务顺序。
- artifact hash 排除 `artifact_sha256` 字段本身。
- 每个 check 保存 `source_refs[{path,json_pointer,sha256}]`。
- 父 evidence index 读取子报告原始字节重算 hash；不能只采信其 status。
- R 绑定冻结 experiment dataset manifest，不因无关新行情追加而失效；有效期按验收矩阵。
- G 总索引24h内、当前 clean code/config/schema/runtime identity 一致。

## 12. 可执行质量命令与产物

权威解释器：`.venv312\Scripts\python.exe`。

| 门禁 | 命令 | 产物 |
|---|---|---|
| 全量Python测试 | `.venv312\Scripts\python.exe -m pytest -q` | `runtime/v2/quality/pytest.txt` |
| Ruff | `.venv312\Scripts\python.exe -m ruff check . --exclude web/frontend/node_modules` | `ruff.txt` |
| Mypy | `.venv312\Scripts\python.exe -m mypy ab_screener paper_trading logic_platform web/backend_app.py` | `mypy.txt` |
| 离线研究状态 | `.venv312\Scripts\python.exe research_status.py --no-token-probe` | `research-status.json` |
| 前端单测 | `npm --prefix web/frontend run test` | `frontend-test.txt` |
| 前端构建 | `npm --prefix web/frontend run build` | dist manifest/hash |
| 浏览器 | `powershell -NoProfile -File scripts/run_browser_acceptance.ps1` | Playwright HTML/JSON |
| 性能 | `.venv312\Scripts\python.exe -m pytest -q -m performance` | `performance.json` |
| 故障注入 | `.venv312\Scripts\python.exe -m pytest -q -m fault_injection` | `fault-injection.json` |
| 恢复 | `powershell -NoProfile -File scripts/restore_backup.ps1 -VerifyOnly` | `restore-report.json` |
| 真实数据 | `.venv312\Scripts\python.exe -m paper_trading.real_data_gate --days 730 --report runtime/gates` | gate JSON |

所有命令 exit 0 才算执行成功；gate status 仍按验收矩阵判断。浏览器脚本只停止自己创建的PID。五日 soak 不能在一个实现 turn 内伪造；每天写 `runtime/v2/soak/<date>.json`，第五个完成交易日由独立审计续验。

