# NTM P2 实施计划：接线（run_screener / desk-supplement / health）

> 日期：2026-08-22 ｜ 状态：**待开工（交给实施 agent；前置条件：P1 验收通过）**
> 上游：`E:\CODEX\national-team-monitor\docs\INTEGRATION-PLAN.md`（§3 A4、A5）
> 目标：让 P1 的 overlay 与快照适配器进入生产路径，同时保证「未配置快照时行为与现状完全一致」。

## 0. 前置条件

- P1 完成且验收通过（`tests/test_national_team_overlay.py`、`tests/test_ntm_client.py` 全绿）；
- 本 Phase 依旧**不新增 API 路径**（desk-supplement 是既有路径的字段扩展，health 同理）。

## 1. Step 0：勘察（先做，据实记录）

```powershell
grep -rn "regime_overlays()\|defensive_overlay_v1\|apply_regime_overlays" ab_screener run_screener.py web tests | Select-Object -First 40
grep -n "detect_regime\|allow_new_entries\|regime" run_screener.py | Select-Object -First 40
grep -n "build_desk_supplement\|national_team\|astock" ab_screener/intelligence/desk_supplement.py
```

需要回答（写进交付说明）：
1. `defensive_overlay_v1` 当前是否已在**生产路径**被 evaluate？（预期：只注册未接线 —— 若已在生产接线，接线方式照抄其模式）
2. `run_screener.py` 中 `detect_regime(...)` 的调用点行号与 `regime.allow_new_entries` 的消费点行号；
3. `desk_supplement.py` 返回 dict 的组装位置与既有测试对返回结构的断言方式。

## 2. Step 1：新增 overlay 汇总执行器

文件：`ab_screener/regimes/evaluate_overlays.py`（新）

```python
"""regime overlay 汇总执行（P2）：任一 overlay 禁止 → 禁止（fail-closed）。"""
from __future__ import annotations

from ab_screener.regimes.contracts import OverlayDecision, OverlayInput
from ab_screener.regimes.registry import regime_overlays


def apply_regime_overlays(overlay_input: OverlayInput) -> OverlayDecision:
    for overlay_id, entry in regime_overlays().items():
        try:
            decision = entry["evaluate"](overlay_input)
        except Exception:  # noqa: BLE001 - overlay 异常按禁止处理（fail-closed）
            return OverlayDecision(allow_new_entries=False, mode="defensive",
                                   reason=f"{overlay_id} 异常(fail-closed)")
        if not decision.allow_new_entries:
            return decision  # 返回第一个禁止项，保留其 reason/mode
    return OverlayDecision(allow_new_entries=True, mode="neutral",
                           reason="全部 overlay 放行")
```

约束：
- 纯函数：不触网不读库；`regime_overlays()` 来自注册表模块；
- 顺序无关性：结果不依赖注册表 dict 顺序（任一禁止即禁止）；
- fail-closed 注释保留。

## 3. Step 2：run_screener 接线

文件：`run_screener.py`（按 Step 0 勘察的**实际行号**修改，下方为逻辑示意）

```python
# 顶部 import（与现有 import 风格一致）
from ab_screener.integrations.ntm_client import read_ntm_snapshot
from ab_screener.regimes.contracts import OverlayInput
from ab_screener.regimes.evaluate_overlays import apply_regime_overlays

# 在 detect_regime(...) 之后、消费 allow_new_entries 之前：
nt = read_ntm_snapshot()
overlay_dec = apply_regime_overlays(OverlayInput(
    market_regime=regime.regime,
    benchmark_trend=0.0,      # 本 Phase 不新造指标：沿既有基准口径或 0.0
    drawdown_from_peak=0.0,   # 同上；真实回撤接入属后续迭代
    national_team=(nt or {}).get("resonance"),
))
regime.allow_new_entries = regime.allow_new_entries and overlay_dec.allow_new_entries
```

约束：
- **行为等价**：未配置 `NTM_SNAPSHOT_PATH`（read 返回 None）时，`overlay_dec.allow_new_entries=True`，上式结果与现状逐位一致（防守 overlay 的规则不变）；
- **快照过期/损坏**：read 返回 None → 同样等价现状；
- `regime.to_dict()` 输出结构**不新增键**（可不动）；如确需传递原因，加可选键并保证既有测试断言兼容（优先不加）；
- 不得把快照读取放进扫描子进程热路径循环（每次扫描读一次即可）；
- `benchmark_trend/drawdown_from_peak` 如仓库已有可用值（如组合回撤），优先复用真实值并在交付说明注明出处；没有就传 0.0，**不得新造计算**。

## 4. Step 3：desk-supplement 扩展 national_team 段

文件：`ab_screener/intelligence/desk_supplement.py`
在返回 dict 中新增（只读）：

```python
"national_team": {
    "enabled": bool, "reachable": bool, "as_of": str | None,
    "verdict": str | None, "red_count": int | None, "green_count": int | None,
    "holders_count": int | None, "seat_alerts_count": int | None,
    "error": str | None,
}
```

实现要点：
- 复用 `ab_screener/integrations/ntm_client.py` 的 `ntm_status()` + 一次 `read_ntm_snapshot()`（只读一次文件，两个函数共享同一份快照，不要读两遍）；
- 快照为 None 时：`enabled` 按路径是否配置，`reachable=False`，其余计数字段 None，`error` 注明原因；
- **不改动** desk_supplement 既有字段与既有测试断言；新字段只增不改。

## 5. Step 4：/api/health 增加 national_team 状态

文件：`ab_screener/api/routers/legacy_misc.py`（按 Step 0 勘察确认实际文件）
在 health 响应（regime 旁）新增：

```python
"national_team": ntm_status(),   # 同样：不触网、读文件失败降级
```

约束：health 端点在扫描/研究期间必须仍即时返回（快照读取是纯文件 IO，一次读取即可，勿循环调用）；响应新增字段不得破坏既有 health 契约测试（若有 health 结构断言，按「新增可选字段」兼容处理，必要时同步测试并说明）。

## 6. Step 5：测试（新增/扩展）

### 6.1 新增 `tests/test_evaluate_overlays.py`

| # | 用例 | 断言 |
|---|---|---|
| E1 | 全放行 | monkeypatch `regime_overlays()` 返回两个 evaluate 恒 True 的 entry → allow=True |
| E2 | 一个禁止 | 其一返回 allow=False + reason「X」→ 汇总 allow=False 且 reason 保留 |
| E3 | 异常 fail-closed | entry evaluate raise → allow=False，reason 含「fail-closed」 |
| E4 | 与真实注册表联测 | 不 patch 注册表：`OverlayInput(national_team={"verdict":"危险共振",…})` → allow=False（证明 P1 overlay 已注册并生效） |

### 6.2 扩展 `tests/test_desk_supplement.py`（仿既有 `test_desk_supplement_gate_fields` 风格新增用例，不改旧断言）

| # | 用例 | 断言 |
|---|---|---|
| D1 | 未配置快照 | `national_team.enabled=False`，`reachable=False`，其余 None，HTTP 仍 200 |
| D2 | 配置有效快照 | monkeypatch 环境变量 + tmp 文件 → `reachable=True`，`verdict` 正确，计数正确 |
| D3 | 快照过期 | 过期 as_of → `reachable=False` 且 `error` 含「过期/缺失」语义 |

### 6.3 health 契约

若仓库存在 health 结构断言测试（如 `test_openapi_contract_v2.py` 或 health 相关用例），按仓库惯例同步新增 `national_team` 字段断言；**不得修改旧断言**。

## 7. Step 6：回归与门禁（提交前必须全绿）

```powershell
<PY> -m pytest tests/test_evaluate_overlays.py tests/test_desk_supplement.py tests/test_ntm_client.py tests/test_national_team_overlay.py -q
<PY> -m pytest tests/test_strategy_plugin_contract.py tests/test_astock_client.py tests/test_openapi_contract_v2.py -q
<PY> scripts/check_architecture.py --strict
```

- OpenAPI 路径总数不变（本 Phase 无新路径；desk-supplement/health 只是字段扩展）；
- `check_architecture.py --strict` exit 0。

## 8. Step 7：手工冒烟（必须做，输出贴进交付说明）

构造三种快照（可用 NTM 或手写临时 JSON，字段按 P1 计划 §1 契约）：

1. **危险共振快照**：`$env:NTM_SNAPSHOT_PATH=<tmp>` → 扫描（或 run_screener 空跑/审计模式）→ 日志/输出出现「禁止开仓」语义且 A 池为空或 allow_new_entries=False；
2. **机会共振快照**：→ 正常放行（A 池按正常逻辑产出）；
3. **不配置路径**：→ 与现状完全一致（对照组）。

冒烟命令建议（以仓库实际入口为准）：
```powershell
<PY> run_screener.py --top 5 --days 160 --workers 0        # 或 audit_funnel.py
<PY> -m uvicorn web.backend_app:app --port 8001 等（desk-supplement 用 curl/TestClient 验证）
```

## 9. 交付证据（返回给我检查）

1. Step 0 勘察结论（三个问题的答案 + 关键行号）；
2. 改动文件清单 + diff 摘要；
3. Step 6 三条命令完整输出（exit code）；
4. Step 7 三组冒烟输出对比；
5. 注明所用 Python 解释器路径。

**禁止事项**：不新增 API 路径；不改写路径（扫描/纸面账本）；不改 NTM 仓库；不修改既有测试断言；benchmark_trend/drawdown_from_peak 不得新造计算。
