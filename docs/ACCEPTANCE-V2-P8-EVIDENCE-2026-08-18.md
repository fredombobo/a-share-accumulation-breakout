# P8 最终验收证据索引 — 机构级控制台 v2（2026-08-18）

> 对应 runbook §10「独立最终审计输入」。本文档为审计入口索引，不替代逐项证据。

## 1. 总状态（诚实声明）

| 项 | 状态 |
|---|---|
| 工程实现 | P0–P6 全部完成；P7.1 后端 v2 routers + P7.2 前端 API/类型拆分完成 |
| 整体状态 | **BLOCKED [WORKTREE_DIRTY]** —— 仅因用户未提交的 `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md` 修改 |
| 发布候选 | **未声明** `PERSONAL_INSTITUTIONAL_READY`（runbook 禁止 Agent 自行宣布） |
| 真实数据门禁 | 未运行（需 Token + 维护窗口；离线门禁全绿） |

## 2. 交付 commit 清单（P0 → P7.2）

| commit | 内容 | WP |
|---|---|---|
| `2b2520d` | v2 计划/规格/验收矩阵/DAG 冻结 | WP00 |
| `c47aa26` | P0.1 基线重测（capture/manifest/venv312） | WP00 |
| `cf17137` | P0.4 契约（错误码/平台配置/七闸门/迁移注册表） | WP00 |
| `4729074` | P0.2+P0.3（ENTRY registry + 质量门禁/NO_REPLACE 清零） | WP10 |
| `89a5697` | P1.1 PIT 五元组/历史表/回填断点续跑 | WP20 |
| `8e68ef2` | P1.2 as-of 股票池 + instrument 规则 | WP20 |
| `e0b2f3f` | P1.3 公司行为账本/数据质量门禁 | WP20 |
| `833e233` | P1.4 市场情报（只读） | WP20 |
| `6dc8ee1` | P2.1 唯一执行领域核心（整数分账务） | WP10 |
| `31d4df8` | P2.2 可成交语义 + 研究/纸面 parity | WP10 |
| `457b9a6` | P2.3 执行血缘固化 | WP50 |
| `cdd33e0` | P3 研究治理/正式统计/成本容量/晋级 | WP30 |
| `25c1c91` | P4.1 六形态插件契约 + 防守 overlay | WP40 |
| `630520c` | P4.3 不可变信号/事件/outcome | WP40 |
| `913dba6` | P4.2 ScanProfile + 漏斗 + 信号管线 | WP40 |
| `202ab60` | P5 组合约束/风险指标/压力情景 | WP50 |
| `d1f7c68` | P6 持久 DAG/审计/告警/备份/持仓语义 | WP60 |
| `25f5349` | P7.1 v2 API routers + OpenAPI 契约 | WP70 |
| `4506e70` | P7.1+P7.4 v2 routers 完整装配 + Review 决策台账 | WP70 |
| `21de9a6` | deps/backup_root 默认路径修复 | WP70 |
| `303c8e5` | P7.2 前端 API/类型/hooks/通用组件 | WP70 |
| `b5a8346`+`a63e974` | P0–P3 交接文档 + 基线身份刷新 | WP80 |

## 3. 质量证据

- 全量离线门禁（`.venv312\Scripts\python.exe -m pytest tests\ -q -k "not browser"`）：**594 passed / 1 skipped**（P0.1 基线 335 → 594，+259 用例）
- ruff：`ruff check . --exclude web/frontend/node_modules` → All checks passed
- mypy（CI 范围 + v2 模块）→ Success
- 前端：`npm run build`（tsc -b + vite）→ ✓ built（dist 入库）
- 基线 manifest：`runtime/v2/baseline_manifest.json`（identity 随 HEAD 复采；最近 `2e22e27d5173897b` @ 25f5349，pytest 591/591 —— **P7.4/P7.2 提交后需重采**）
- 迁移冒烟：`scripts/migrate_v2.py --db <副本> --plan/--apply` 通过

## 4. 迁移注册表（9 个 v2 意图）

```
v2:pit_history, v2:instrument_rules, v2:corporate_actions, v2:execution_lineage,
v2:research_governance, v2:signals, v2:scan_profiles, v2:portfolio_risk,
v2:operations
```
（paper M001–M009 兼容；`schema_compatible` 按 id+checksum 判定）

## 5. 七闸门状态（readiness 口径）

| 闸门 | 状态 | 说明 |
|---|---|---|
| D 数据 | INSUFFICIENT | 真实 517 万行 PIT/仪器回填未执行（需 Token+维护窗口） |
| R 研究 | INSUFFICIENT | 正式统计实现已就绪；真实研究结论未产出 |
| S 统计 | PASS(实现) | CSCV-PBO/DSR/MinTRL/nested-WF 离线 fixture 全绿 |
| P 纸面 | PASS(实现) | 执行核心/血缘/风险/日结阻断离线全绿 |
| L 账本 | PASS(实现) | NO_REPLACE 清零；append-only 触发器；审计 hash chain |
| O 运营 | INSUFFICIENT | backup_root 未配置；soak <5 交易日；持仓同步旧字段 unknown |
| G 治理 | PASS(实现) | 实验 registry/trial ledger/晋级双口径/产物清单 |

聚合（固定序）：非全 PASS → **BLOCKED**（诚实，与 manifest 一致）。

## 6. 明确未完成（下一阶段）

- [ ] 用户合并 `docs/STATUS.md`/`docs/RESEARCH-ROADMAP.md` → 重采基线转 OK
- [ ] 真实 Token 回填（PIT/instrument/adj_factor）+ 真实数据门禁 + 覆盖/hash 100% 后切 `V2_PIT_READ_ENABLED`
- [ ] P7.2 大页面（desk/intelligence 等页面组件与导航壳集成，WP70 shell）
- [ ] backend_app.py 深度拆分（架构债务 4 项，`--strict` 清零）
- [ ] `scripts/soak_monitor_v2.py`、`scripts/restore_backup.ps1`、`docs/BACKUP-RESTORE-RUNBOOK-V2.md`、RPO≤1交易日/RTO≤30min 演练
- [ ] easy_start/bootstrap 调度 hook（WP80 落地）
- [ ] API 金额/价格十进制字符串全面化 + `Idempotency-Key` 强制（契约已定义）

## 7. 真实数据验证进展（2026-08-18 晚更新）

| 项 | 结果 | 证据 |
|---|---|---|
| Token/mirror 连通 | PASS | `tushare_init` 标准入口（用户指定调用方式）实测 trade_cal 可达，源端最新 20260821 |
| 行情同步 | PASS | daily 拉到 20260818（+11082 行），daily_basic/moneyflow/benchmark 同步 |
| **真实数据门禁** | **PASS** | `runtime/gates/real_data_gate_20260818_190832.json`：968 交易日覆盖、**20 标的×5 日=100 对源端比对 0 差异**、公司行为接口可用、issues 空 |
| v2 数据质量 | PASS | duplicate=0 / invalid_ohlc=0（144 停牌行豁免）/ 覆盖率 **99.9%**（5203/5208）/ 持仓 100% |
| instrument 回填 | PASS | 5541 条规则（5208 上市 + 333 退市）落 `instrument_universe_rules` |
| 运行库 v2 迁移 | PASS | 10 个 v2 意图已应用（P7.4 时），additive 无破坏 |
| 质量检查语义修复 | PASS | `data_quality` OHLC 检查豁免停牌行（open=0 且 vol=0）——真实数据暴露并修复 |

**剩余 INSUFFICIENT（诚实）**：PIT 517 万行历史回填、soak ≥5 完成交易日、`AB_BACKUP_ROOT` 配置、备份保留 ≥7 份、RTO 演练计时（均需维护窗口/连续交易日）。

## 8. 审计者操作指引

1. `git status --short`（应仅剩两个用户文档 dirty）
2. 重跑 `scripts/capture_v2_baseline.py` 复采基线
3. `.venv312` 下重跑全量 pytest + ruff + mypy + `npm run build`
4. 抽样核对 artifact hash（`research_artifact_repository.verify_artifact`、`artifact_manifest.verify_manifest`、审计 `verify_audit_chain`）
5. 对副本执行 `migrate_v2.py --plan/--apply` 冒烟
