# V2R-N Agent 交付

> 本文件由实现 Agent 填写。自报完成不等于验收通过；管理者复验后才会更新任务板。
> 结论：`READY_FOR_REVIEW`。覆盖层是**只读信息增强**，不是真实持仓或交易信号。

## 1. 身份

- 任务 ID：V2R-N
- Agent 角色：information-overlay-agent（国家队/机构资金 PIT-safe 只读信息覆盖层）
- 基线 commit（base）：`7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe`（集成基线，禁止改动其历史）
- 分支：`v2r-n`
- worktree 绝对路径：`E:\CODEX\Stock_selection\worktrees\v2r-n`
- 交付 commit（head）：`76d2219a0fef374177e531c4c61cffb166c93f5e`（feature 提交，base 7bbca60 之后仅此 feature + 上一提交为本分支新增）；本 handoff 在后续 `docs(v2r-n)` 提交中（见 §9）
- 完成时间（Asia/Shanghai）：2026-08-23

## 2. 范围核对

Owned paths（任务板 V2R-N.owned_paths）实际修改文件：

- `ab_screener/intelligence/national_team_overlay_v1.py`（新建，领域 PIT 求值）
- `ab_screener/data/adapters/ntm_client.py`（新建，只读适配器）
- `ab_screener/application/evaluate_overlays.py`（新建，只读汇总/注释）
- `configs/intelligence/national_team_overlay_v1.yaml`（新建，带 version/config_hash）
- `tests/test_national_team_overlay.py`（新建）
- `tests/test_ntm_client.py`（新建）
- `tests/test_evaluate_overlays.py`（新建）
- `docs/handoffs/V2R-N.md`（本文件）

是否全部位于 owned_paths：**是**。
是否触碰 protected/shared paths：**否**（未改 `web/backend_app.py`、`app_factory.py`、`configs/platform_v2.yaml`、前端 dist、regimes registry、既有测试断言）。
是否修改既有文件：**否**（全部为新增文件；对既有文件零改动）。

```bash
git status --short                    # 提交时工作区干净
git diff --stat 4d30dd2..76d2219      # owned diff（7 files, 1220 insertions）
# ab_screener/application/evaluate_overlays.py        | 130 +++
# ab_screener/data/adapters/ntm_client.py             | 267 +++
# ab_screener/intelligence/national_team_overlay_v1.py| 104 +++
# configs/intelligence/national_team_overlay_v1.yaml  |  15 +
# tests/test_evaluate_overlays.py                     | 292 +++
# tests/test_national_team_overlay.py                 | 189 +++
# tests/test_ntm_client.py                            | 223 +++
# 7 files changed, 1220 insertions(+)
```

## 3. 缺口与设计

### 3.1 原始缺口

V2R-N 任务板要求实现「国家队/机构资金等信息增强只读覆盖层」：覆盖层只解释、标记或研究排序，**不得成为 A/B 池资格、仓位权重或订单输入**；历史决策只读 `available_at <= decision_at` 的信息；缺 source / 权限不足 / 字段缺失 → 结构化 INSUFFICIENT，不伪造供应商能力。

参考设计 `2026-08-22-ntm-p1-overlay.md` / `2026-08-22-ntm-p2-wiring.md` 与派工总表冲突时，**以 V2R-N 只读边界为准**：本实现不把覆盖层接进 `run_screener` 开仓许可（P2 的 allow_new_entries 接线不属于本任务），也不注册进 `ab_screener/regimes/registry.py`（该 registry 被 `test_strategy_plugin_contract.py` 断言 `len==1`，且是开仓许可语义，覆盖层不得进入）。

### 3.2 设计

三层职责分离：

1. **适配器** `ab_screener/data/adapters/ntm_client.py`：把供应商固定离线原始响应解析为领域记录 `NationalTeamObservation`（八元组 + 载荷）。解析失败返回结构化 `OverlayInsufficient`（vendor_unavailable / missing_source / insufficient_permission / missing_fields / unknown_verdict / invalid_timestamp），**不抛无结构异常、不伪造字段**。只读本地文件，不触网。
2. **领域层** `ab_screener/intelligence/national_team_overlay_v1.py`：PIT 求值 `evaluate(record, *, decision_at)`（available_at > decision_at → `future_information`；无效 decision_at → `invalid_decision_at`）；历史读取 `records_for_decision` 只返回 `available_at <= decision_at` 的记录；`observation_hash` 确定性指纹。纯函数，无 IO。
3. **应用层** `ab_screener/application/evaluate_overlays.py`：`evaluate_overlays` 汇总覆盖层求值结果（只读注释）；`annotate_decision` 把观测附加到决策**副本**，只新增 `annotations`/`disclaimer` 键，A/B 资格/目标仓位/订单字段逐字节不变。

配置 `configs/intelligence/national_team_overlay_v1.yaml` 带 `version: v1` 与 `config_hash`（规范化配置去自身后 SHA-256 前 16 位，测试复算校验）。

## 4. 数据契约

### 4.1 领域记录 `NationalTeamObservation`

| 字段 | 类型 | 语义 |
|---|---|---|
| observation_at | str（+08:00 ISO） | 观测发生时刻 = 供应商 generated_at |
| effective_at | str（+08:00 ISO） | 观测对应的市场日期 = 快照 as_of（按 +08:00 零点） |
| available_at | str（+08:00 ISO） | 信息真实可用时刻（PIT 门禁：仅 available_at <= decision_at 可读） |
| ingested_at | str（+08:00 ISO） | 本地入库时刻（缺省 = available_at） |
| source | str | 来源（必须为 `ntm`，未知来源拒绝） |
| revision | str | 修订（`schema_v1_<紧凑时间戳>`，确定性） |
| confidence | str | low / medium / high；degraded/warnings 非空 → 封顶 low |
| evidence_refs | tuple[str, ...] | 证据引用（可空） |
| verdict / red_count / green_count / total / per_etf | str / int / int / int / tuple[dict] | resonance 载荷 |

### 4.2 结构化 `OverlayInsufficient`

`status="INSUFFICIENT"` + `reason`（稳定机器码）+ `detail` + `decision_at`。reason 枚举：

- `vendor_unavailable`：快照未配置 / None / 文件缺失 / 损坏 JSON
- `missing_source`：source 缺失或非 `ntm`
- `insufficient_permission`：permission.granted 非 true，或 scope 缺少 `resonance`
- `missing_fields`：as_of / generated_at / confidence / schema_version / verdict / counts / evidence_refs（非空时）缺失或非法
- `unknown_verdict`：verdict 不在 {危险共振, 机会共振, 中性}（不解释，不伪造）
- `invalid_timestamp`：外部时间无时区或不可解析（不默认补 Asia/Shanghai）
- `future_information`：available_at > decision_at（领域层）
- `invalid_decision_at`：decision_at 无时区或不可解析（领域层，fail-closed）

所有时间为带时区（+08:00）时间戳；无时区一律拒绝/INSUFFICIENT。

## 5. PIT 对抗用例

| 用例 | 断言 |
|---|---|
| available_at=18:30, decision_at=10:00（同日更早） | evaluate → INSUFFICIENT `future_information` |
| 历史读取（决策 10:00） | `records_for_decision` 只返回 09:00 可用记录，18:30 未来记录不可见 |
| 全部记录均晚于决策时点 | `records_for_decision` → 空元组 |
| decision_at 无时区 / 不可解析 | evaluate → INSUFFICIENT `invalid_decision_at`；records_for_decision → 空（fail-closed） |
| 外部 generated_at 无时区 / 不可解析 | parse → INSUFFICIENT `invalid_timestamp`（不默认补时区） |
| 观测记录 4 个时间字段 | 均带 +08:00，`datetime.fromisoformat(...).tzinfo is not None` |

## 6. Overlay on/off parity（管理者可复跑）

fixture：`tests/test_evaluate_overlays.py::frozen_parity_case`（冻结行情 + 冻结扫描输入，附 `frozen_hash`）。

- 冻结输入：`FROZEN_MARKET`（指数/趋势/回撤/成交额）+ `FROZEN_SCAN_INPUT`（5 只候选、资格阈值、现金、A 池容量、入场定义）。
- `produce_decision(frozen_input)`：纯函数 → `{a_pool_eligible, b_pool_eligible, target_position, orders}`。
- overlay on：`evaluate_overlays(FROZEN_RAW, decision_at=...)` → `PASS`；`annotate_decision` 只新增 `annotations`/`disclaimer`。
- 断言：overlay off 与 on 的 `a_pool_eligible / b_pool_eligible / target_position / orders` **逐字段一致**（整体 dict 相等）。
- 供应商不可用：raw=None → `INSUFFICIENT`，注释为空，决策闭环不受影响、不抛异常。

复跑命令：`<PY> -m pytest tests/test_evaluate_overlays.py -q`。

## 7. 权限失败

- `permission.granted=False` → INSUFFICIENT `insufficient_permission`。
- `permission.scope` 缺 `resonance` → INSUFFICIENT `insufficient_permission`。
- `source` 未知（如 `unknown_vendor`）→ INSUFFICIENT `missing_source`（不伪造供应商能力）。
- 外部字段或权限不确定 → 一律结构化 INSUFFICIENT，绝不带默认值硬读。

## 8. 验收证据

### 8.1 RED（修改前失败证据，exit=2）

```text
3 errors in 0.35s
ERROR tests\test_national_team_overlay.py
ERROR tests\test_ntm_client.py
ERROR tests\test_evaluate_overlays.py
ModuleNotFoundError: No module named 'ab_screener.intelligence.national_team_overlay_v1'
ModuleNotFoundError: No module named 'ab_screener.data.adapters.ntm_client'
ModuleNotFoundError: No module named 'ab_screener.application.evaluate_overlays'
```

（副本存于本地 `/tmp/v2rn_red_pytest.txt`。）

### 8.2 GREEN（实现后）

```text
$ <PY> -m pytest tests/test_national_team_overlay.py tests/test_ntm_client.py tests/test_evaluate_overlays.py -q
44 passed in 0.90s

$ <PY> -m ruff check ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
All checks passed!

$ <PY> -m mypy ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
Success: no issues found in 3 source files
```

### 8.3 相关既有回归

```text
$ <PY> -m pytest tests/test_strategy_plugin_contract.py tests/test_desk_supplement.py tests/test_astock_client.py tests/test_openapi_contract_v2.py tests/test_architecture_boundaries.py -q
27 passed in 7.61s

# 扩展聚焦回归（intelligence / data adapter / api / architecture）
$ <PY> -m pytest tests/test_intelligence_catalog.py tests/test_intelligence_supplement_api.py tests/test_event_timeline_pit.py tests/test_astock_client.py tests/test_desk_supplement.py tests/test_strategy_plugin_contract.py tests/test_openapi_contract_v2.py tests/test_architecture_boundaries.py -q
39 passed in 98.99s
```

（`test_strategy_plugin_contract.py` 仍断言 `len(regime_overlays())==1`：覆盖层未注册进开仓许可 registry。）

### 8.4 全量回归说明

`pytest tests/ --ignore=tests/browser` 在本 worktree 无法完整跑完，且存在**环境性**收集错误：`test_daily_run_manifest.py` 导入 `web.backend_app` 时 `assert_schema_compatible` 因 worktree 的 `runtime/stock_data.db` 尚未跑 `migrate_v2.py`（`MIGRATION_PENDING` 列表）而抛错——与本任务无关（本任务全部为新增文件，不触碰 DB / backend / schema_check）。全量执行 19 分钟后仍停留在 DB 密集用例，未产出结论，已停止；不影响上述定向验收命令结果。

## 9. 提交与回滚

- 提交 1（feature，交付 head）：`git commit` 内容 = 3 模块 + 1 配置 + 3 测试。
- 提交 2（本 handoff）：`docs(v2r-n): add V2R-N handoff`。
- 回滚：本分支对既有文件零修改；回滚 = `git revert <head>` 或直接删除 7 个新增 owned 文件与 handoff。不触碰基线前历史，无 force-push。

## 10. 未决项

1. 覆盖层**未接线**到 desk-supplement / health / 扫描路径——按 V2R-N 只读边界，接线属 V2R-G / 后续集成任务，需单独评审。
2. 供应商真实 NTM 快照 schema 若与固定离线契约不符，需按 §4 契约重新验证；本交付全部测试用固定离线原始响应，无网络依赖。
3. `config_hash` 计算约定（去掉 `config_hash` 自身后 `canonical_json` SHA-256 前 16 位）已在配置注释与 `tests/test_evaluate_overlays.py` 固定，后续改配置需同步重算。

## 11. 最终声明

状态：`READY_FOR_REVIEW`。本覆盖层是**只读信息增强/研究观察**，不是真实持仓或交易信号；不进入 A/B 池资格、仓位权重或订单。
