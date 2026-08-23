# V2 Remediation Wave 2 Agent 派工总表

## 0. 当前裁决

- Wave 1：`ACCEPTED`。
- 全系统：仍为 `BLOCKED`，不得宣布 `PERSONAL_INSTITUTIONAL_READY`。
- 立即并行：V2R-S、V2R-R、V2R-N。
- 暂缓：V2R-O2；只有 V2R-S 经管理者验收并合并后才释放。
- 业务集成代码锚点：`5cf7088`。
- 每个 Agent 的精确 base SHA、分支和绝对 worktree 路径，以管理者创建在该分支中的 `docs/handoffs/V2R-*-START-2026-08-23.md` 为准。

## 1. 所有 Agent 的共同规则

1. 只在分配的独立 worktree 工作，不进入主工作区，也不自行 rebase/merge 其他 Agent 分支。
2. 先运行 START 文件的基线命令，写一个能证明缺口的失败测试，再做最小实现。
3. 只修改任务 `owned_paths`；需要共享文件时停止并在 handoff 提交管理者，不得越权改 `web/backend_app.py`、`app_factory.py`、`configs/platform_v2.yaml` 或前端 dist。
4. 禁止写生产 DB；副本和临时库必须在 handoff 写明绝对路径、指纹和清理/保留方式。
5. `LIVE_TRADING_ENABLED`、PIT 正式读、执行写、风险 enforce、自动调度均保持 false。
6. 研究任务允许诚实 FAIL/INSUFFICIENT；不得为了 PASS 调阈值、删除窗口或隐藏成本。
7. 每个行为至少有正常与失败/边界测试；随机过程固定种子；测试不得依赖互联网。
8. 完成时提交代码和 `docs/handoffs/V2R-*.md`，报告 base/head、diff、RED/GREEN、测试、DB、契约变化、风险、回滚和未决项。
9. Agent 只能声明 `READY_FOR_REVIEW`，不能改任务板状态或宣布系统就绪。

## 2. V2R-S — 信号生命周期闭环

### 目标

把六形态插件、扫描结果、不可变 signal observation、纸面成交和 outcome 真正接成生产路径，同时保持未来函数和实验插件硬隔离。

### Owned paths

- `ab_screener/strategies/**`
- `ab_screener/application/signal_pipeline.py`
- `ab_screener/application/signal_outcomes.py`
- `ab_screener/domain/signal*.py`
- `ab_screener/data/signal*.py`
- `ab_screener/api/routers/legacy_scan.py`
- `tests/test_signal*.py`
- `tests/test_strategy*.py`
- `docs/handoffs/V2R-S.md`

### 强制行为

1. 同一 `scan_run_id + strategy_version + instrument + decision_at` 重放幂等；新 revision 追加，不覆盖历史 observation。
2. EXPERIMENTAL 只允许研究/观察，不得进入 A 池、生成买单或改变目标仓位。
3. 订单确认不等于 ENTERED；只有实际正数量 fill 才进入，零成交/拒绝/过期不得进入。
4. ret_5/10/20 只在对应交易日完成且 `available_at <= calculation_at` 后回填；UNFILLABLE 保持 NULL。
5. 写入、重放、成交和 outcome 均使用临时数据库，证明没有覆盖历史行。

### 最小质量门

```powershell
& $py -m pytest tests/test_signal_observations.py tests/test_signal_lifecycle_v2.py tests/test_signal_outcomes.py tests/test_signals_v2.py tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py -q
& $py -m ruff check ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py ab_screener/api/routers/legacy_scan.py
& $py -m mypy ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py
```

## 3. V2R-R — 可信研究证据

### 目标

在当前真实本地历史数据上完成固定口径的 600 股、step=5、含净成本 IS/OOS/WF、双基线和反过拟合证据；任务成功定义是“证据完整可复述”，不是强求策略 PASS。

### Owned paths

- `ab_screener/research/**`（仅在证明统计实现缺陷后修改）
- `tests/test_*research*.py`
- `tests/test_*walkforward*.py`
- `runtime/v2/research_*/**`
- `docs/handoffs/V2R-R.md`

### 强制顺序

1. 先记录 research-status、数据交易日数、latest date、数据库指纹、代码 SHA、配置 hash；不是 full 或少于 730 交易日则提交 `INSUFFICIENT_EVIDENCE`，不得伪造运行。
2. 冻结请求：strategy A（需要时按同口径补 B）、`mode=grid`、`max_codes=600`、`step=5`、自动 IS/OOS 窗、完整成本。
3. 同一 task 只查询 status，不重复 POST；保存 task_id、开始/结束时间和产物 SHA-256。
4. 报告必须有随机/MA 基线、至少 3 个 WF 窗、OOS 净指标、成本压力、PBO、DSR、MinTRL、容量、样本与身份。
5. verdict 必须由冻结门禁自动得出；FAIL/INSUFFICIENT 时 `candidate_eligible=false`，PASS 也不得自动进 A 池或下单。
6. 如果发现统计代码缺陷，先用固定 fixture 复现并单独提交修复，然后从头重跑，不沿用旧报告。

### 最小质量门

```powershell
& $py -m pytest tests/test_*research*.py tests/test_*walkforward*.py -q
& $py -m ruff check ab_screener/research
& $py -m mypy ab_screener/research
```

Handoff 必须附一页人话结论：样本、成本、OOS/WF、是否 beat 双基线、反过拟合、是否可作为候选参数，以及下一步；严禁只贴 JSON。

## 4. V2R-N — PIT-safe 只读信息覆盖层

### 目标

实现国家队/机构资金等信息覆盖层，增强解释和研究观察，但用逐项 parity 证明它不改变 A/B 池资格、目标仓位和订单。

### Owned paths

- `ab_screener/intelligence/national_team_overlay_v1.py`
- `ab_screener/data/adapters/ntm_client.py`
- `ab_screener/application/evaluate_overlays.py`
- `configs/intelligence/national_team_overlay_v1.yaml`
- `tests/test_national_team_overlay.py`
- `tests/test_ntm_client.py`
- `tests/test_evaluate_overlays.py`
- `docs/handoffs/V2R-N.md`

### 强制行为

1. 领域记录包含 `observation_at/effective_at/available_at/ingested_at/source/revision/confidence/evidence_refs`。
2. 历史决策只读 `available_at <= decision_at`；无 source、权限不足或字段缺失返回结构化 INSUFFICIENT，不伪造供应商能力。
3. 单元测试完全离线、固定原始响应和时点；adapter 与领域逻辑分离。
4. 同一输入启用/禁用 overlay 后，A/B 资格、目标仓位、订单逐字段一致；覆盖层只能解释、标记或研究排序。
5. 遵守 `2026-08-22-ntm-p1-overlay.md` 和 `2026-08-22-ntm-p2-wiring.md`，冲突时以本任务的只读边界为准。

### 最小质量门

```powershell
& $py -m pytest tests/test_national_team_overlay.py tests/test_ntm_client.py tests/test_evaluate_overlays.py -q
& $py -m ruff check ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
& $py -m mypy ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
```

## 5. V2R-O2 释放条件

V2R-O2 仍为 `blocked`。管理者只有在 V2R-S 满足以下条件后才创建 O2 worktree：

- 信号写入幂等；
- fill 驱动 ENTERED；
- outcome 时点合法；
- EXPERIMENTAL 硬隔离；
- 定向测试、Ruff、Mypy 通过；
- S 已合并到新的精确 integration SHA。

O2 不得提前在旧 base 开工，因为 EOD DAG 的 signal outcome 步骤依赖 S 的最终接口。

## 6. 管理者验收顺序

1. 检查 base/head、owned paths、提交历史和生产数据副作用；
2. 阅读实现并做 PIT/未来函数/风控/隔离审查；
3. 复跑 Agent 命令和至少一条管理者对抗用例；
4. 判定 ACCEPTED 或带缺陷编号退回；
5. S 接受后先释放 O2；R/N 可独立验收但不改变系统总状态；
6. Wave 2 全部完成后才释放 Q2/G/P8。

