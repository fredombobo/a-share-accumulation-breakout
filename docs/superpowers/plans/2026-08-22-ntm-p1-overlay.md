# NTM P1 实施计划：regime overlay + ntm_client + 测试

> 日期：2026-08-22 ｜ 状态：**待开工（交给实施 agent）**
> 上游：`E:\CODEX\national-team-monitor\docs\INTEGRATION-PLAN.md`（§2 快照契约、§3 A1-A5）
> 本 Phase 不新增任何 API 路径、不碰写路径（扫描/纸面账本），只新增纯函数与只读适配器。

## 0. 目标

在 accumulation_breakout 内实现「国家队资金」regime overlay 与 NTM 快照只读适配器：

1. `OverlayInput` 增加可选字段 `national_team`（向后兼容）；
2. 新增 `ab_screener/regimes/national_team_overlay_v1.py`（注册到 overlay 注册表）；
3. 新增 `configs/regimes/national_team_overlay_v1.yaml`；
4. 新增 `ab_screener/integrations/ntm_client.py`（读 NTM snapshot.json，降级 None）；
5. 新增两个测试文件并跑绿；
6. 既有回归 + 架构门禁通过。

## 1. 背景与契约（实施 agent 必读）

NTM（`E:\CODEX\national-team-monitor`）的 `python cli.py snapshot --fetch --out <path>` 输出：

```jsonc
{
  "schema_version": 1,
  "as_of": "2026-08-22",           // 数据日期（YYYY-MM-DD）
  "generated_at": "…",
  "resonance": {
    "verdict": "危险共振",          // 危险共振 | 机会共振 | 中性
    "red_count": 1, "green_count": 4, "total": 5,
    "per_etf": [ { "code": "510300", "verdict": "…", "red": …, "green": …,
                   "indicators": {…} } ]
  },
  "holders": [ … ],                // 股东变动告警（P3/P4 才消费）
  "seat_alerts": [ … ],            // 席位告警（P3/P4 才消费）
  "degraded": [ … ], "warnings": [ … ]
}
```

已确认决策（INTEGRATION-PLAN §8）：
- **A2**：`机会共振` → 仅放行（`allow_new_entries=True`，`mode="neutral"`，不得置 aggressive）；
- **PIT**：`as_of` 按**交易日**滞后 >5 天 → 快照视为过期 → 一律按「无信号」处理。

## 2. Step 0：环境验证（先做，失败即停）

```powershell
# 1) 确定仓库内可用 Python（依次探测）
C:\Python314\python.exe --version
C:\Python312\python.exe --version
C:\Users\13818\anaconda3\python.exe --version
# 2) 确认能用它跑通既有测试（选一个绿的最小集）
<PY> -m pytest tests/test_astock_client.py tests/test_desk_supplement.py -q
# 3) 清理代理污染（AB AGENTS.md 硬约束）
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
$env:http_proxy=$env:https_proxy=$env:all_proxy=$null
```

若三处 Python 均无法跑绿最小集，**停工回报**（不要换解释器硬上、不要动 requirements）。

## 3. Step 1：扩展 OverlayInput

文件：`ab_screener/regimes/contracts.py`

```python
@dataclass(frozen=True)
class OverlayInput:
    market_regime: str
    benchmark_trend: float
    drawdown_from_peak: float
    allow_new_entries_override: bool | None = None
    national_team: dict | None = None      # 新增：NTM 快照的 resonance 段；None=未配置/过期
```

约束：
- 新字段**必须放最后**且带默认值（frozen dataclass 向后兼容，既有构造点零改动）；
- 不改动其它字段名/顺序/类型。

## 4. Step 2：新增 national_team_overlay_v1

文件：`ab_screener/regimes/national_team_overlay_v1.py`
模板：`ab_screener/regimes/defensive_overlay_v1.py`（结构逐段对齐）。

```python
"""国家队资金 overlay v1（NTM 集成 P1，INTEGRATION-PLAN §3 A2）。

规则：
- 人工覆盖优先（与防守 overlay 一致）；
- national_team=None（未配置/快照过期）→ 放行，不改变既有决策；
- 危险共振 → 禁止新开仓（mode=defensive）；
- 机会共振 → 仅放行（mode=neutral，不置 aggressive，mode 由价格 regime 决定）；
- 中性/未知 → 放行。
"""
from __future__ import annotations

from ab_screener.regimes.contracts import OverlayDecision, OverlayInput
from ab_screener.regimes.registry import register_regime_overlay
from ab_screener.strategies.contracts import StrategySpec

NATIONAL_TEAM_OVERLAY_ID = "national_team_overlay_v1"

SPEC = StrategySpec(
    strategy_definition_id=NATIONAL_TEAM_OVERLAY_ID,
    version="v1",
    economic_assumption=(
        "国家队（中央汇金）宽基ETF五灯共振危险期，开仓胜率恶化，暂停新开仓；"
        "机会共振为左侧布局窗口，仅放行不进攻"
    ),
    failure_conditions="NTM快照缺失/过期被误判为信号；快照滞后导致危险共振漏挡",
    pit_test="仅使用 overlay_input.national_team 中携带、生成时点已可用的快照数据",
    golden_fixture="tests/fixtures/national_team_overlay_v1_golden.json（待生成）",
    config_path="configs/regimes/national_team_overlay_v1.yaml",
)


def evaluate(overlay_input: OverlayInput, config: dict | None = None) -> OverlayDecision:
    if overlay_input.allow_new_entries_override is not None:
        return OverlayDecision(
            allow_new_entries=overlay_input.allow_new_entries_override,
            reason="人工覆盖", mode="neutral",
        )
    nt = overlay_input.national_team
    if not isinstance(nt, dict) or not nt:
        return OverlayDecision(
            allow_new_entries=True, reason="NTM无信号（未配置/快照过期）", mode="neutral",
        )
    verdict = nt.get("verdict")
    red = nt.get("red_count", 0)
    green = nt.get("green_count", 0)
    if verdict == "危险共振":
        return OverlayDecision(
            allow_new_entries=False, mode="defensive",
            reason=f"国家队危险共振（红{red}/绿{green}）",
        )
    if verdict == "机会共振":
        return OverlayDecision(
            allow_new_entries=True, mode="neutral",
            reason=f"国家队机会共振（红{red}/绿{green}），仅放行，mode由价格regime决定",
        )
    return OverlayDecision(allow_new_entries=True, mode="neutral", reason="NTM中性")


register_regime_overlay(NATIONAL_TEAM_OVERLAY_ID, SPEC, evaluate)
```

约束：
- `evaluate` 是**纯函数**：不触网、不读文件、不读库、不读环境变量；只能读入参；
- 必须与 defensive_overlay 一样**模块导入时注册**（模块内最后一行）；
- `mode` 语义严格按上表（机会共振时 mode 必须为 `"neutral"`，不是 `"aggressive"`）。

## 5. Step 3：注册导出

文件：`ab_screener/regimes/__init__.py`
仿现有 defensive 导入，追加：

```python
from ab_screener.regimes.national_team_overlay_v1 import (  # noqa: F401
    NATIONAL_TEAM_OVERLAY_ID, SPEC, evaluate,
)
```

（若现有 `__init__.py` 只导出契约/注册表符号而不直接 import 模块，则以**与 defensive_overlay_v1 完全相同的导入方式**处理，保持一致性。）

## 6. Step 4：配置文件

文件：`configs/regimes/national_team_overlay_v1.yaml`
参考 `configs/regimes/defensive_overlay_v1.yaml` 的最小结构：

```yaml
enabled: true
note: 国家队五灯共振 overlay（NTM 集成 P1）。危险共振禁止新开仓；机会共振仅放行。
```

（`enabled` 字段本 Phase 不消费，仅为 P2 接线预留；yaml 必须可被 AB 既有配置加载器正常解析。）

## 7. Step 5：新增 ntm_client

文件：`ab_screener/integrations/ntm_client.py`
模板纪律对齐：`ab_screener/integrations/astock_client.py`（只读、失败降级、不抛异常）。

```python
"""NTM 快照只读适配器（INTEGRATION-PLAN §3 A3）。

只读本地 JSON 文件：不触网、不读库、不写任何账本；
文件缺失/损坏/过期一律 None 降级（与 astock_client 同纪律）。
PIT：as_of 按交易日滞后判定，超过 max_lag_days 视为过期。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_LAG_DAYS = 5  # 交易日
SCHEMA_VERSION = 1


def snapshot_path(env: dict[str, str] | None = None) -> str:
    raw = (env or os.environ).get("NTM_SNAPSHOT_PATH", "")
    return str(raw or "").strip()


def is_fresh(as_of: str, max_lag_days: int = DEFAULT_MAX_LAG_DAYS,
             today: str | None = None) -> bool:
    """as_of(YYYY-MM-DD) 与今天之间的工作日数 ≤ max_lag_days。"""
    try:
        d = dt.date.fromisoformat(as_of)
        t = dt.date.fromisoformat(today) if today else dt.date.today()
    except ValueError:
        return False
    if d > t:
        return False
    lag = 0
    cur = d + dt.timedelta(days=1)
    while cur <= t:
        if cur.weekday() < 5:
            lag += 1
        cur += dt.timedelta(days=1)
    return lag <= max_lag_days


def read_ntm_snapshot(path: str | None = None,
                      max_lag_days: int = DEFAULT_MAX_LAG_DAYS,
                      today: str | None = None) -> dict[str, Any] | None:
    p = Path(path if path is not None else snapshot_path()).expanduser()
    if not str(p) or not p.is_file():
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(snap, dict) or snap.get("schema_version") != SCHEMA_VERSION:
        return None
    as_of = snap.get("as_of")
    if not isinstance(as_of, str) or not is_fresh(as_of, max_lag_days, today):
        return None
    return snap


def ntm_status(path: str | None = None) -> dict[str, Any]:
    """对齐 probe_astock 风格的状态字典。"""
    p = path if path is not None else snapshot_path()
    if not p:
        return {"enabled": False, "reachable": False, "as_of": None,
                "verdict": None, "error": None}
    snap = read_ntm_snapshot(p)
    if snap is None:
        return {"enabled": True, "reachable": False, "as_of": None,
                "verdict": None, "error": "快照缺失/损坏/过期"}
    r = snap.get("resonance") or {}
    return {"enabled": True, "reachable": True, "as_of": snap.get("as_of"),
            "verdict": r.get("verdict"), "error": None}
```

约束：
- 禁止 import sqlite3 / subprocess / tushare / requests；
- 只允许 json / os / pathlib / datetime / typing；
- 任何异常路径返回 None/False，**不得 raise**；
- `today` 参数必须支持注入（测试用），默认取系统日期。

## 8. Step 6：测试（新文件 2 个）

AB 测试约定：pytest 风格、放 `tests/`、可仿 `tests/test_astock_client.py` / `tests/test_strategy_plugin_contract.py`。

### 8.1 `tests/test_national_team_overlay.py`

| # | 用例 | 断言 |
|---|---|---|
| T1 | 危险共振 block | `evaluate(OverlayInput(market_regime="neutral", benchmark_trend=0.0, drawdown_from_peak=0.0, national_team={"verdict":"危险共振","red_count":4,"green_count":1}))` → `allow_new_entries is False`，`mode=="defensive"`，reason 含「危险共振」 |
| T2 | 机会共振仅放行 | `national_team={"verdict":"机会共振",…}` → `allow_new_entries is True` **且 `mode=="neutral"`**（不是 aggressive） |
| T3 | 中性放行 | `{"verdict":"中性"}` → allow=True, mode=neutral |
| T4 | national_team=None | → allow=True, reason 含「无信号」 |
| T5 | 未知 verdict | `{"verdict":"乱七八糟"}` → allow=True（按中性处理） |
| T6 | 人工覆盖优先 | `allow_new_entries_override=False` + 机会共振 → allow=False, reason=「人工覆盖」 |
| T7 | 注册唯一 | `import ab_screener.regimes` 后 `regime_overlays()` 含 `national_team_overlay_v1`；重复 import 不抛 `OverlayRegistryError` |
| T8 | 覆盖前既有 overlay 不变 | `regime_overlays()` 仍含 `defensive_overlay_v1`，其 evaluate 行为回归（直接复用/不动既有用例） |

### 8.2 `tests/test_ntm_client.py`

| # | 用例 | 断言 |
|---|---|---|
| C1 | 未配置路径 | `snapshot_path({})==""`；`ntm_status(path="")` → enabled=False, reachable=False |
| C2 | 文件缺失 | 不存在路径 → `read_ntm_snapshot(p) is None` |
| C3 | 损坏 JSON | 写非法内容 → None（不 raise） |
| C4 | schema 不匹配 | `schema_version: 99` → None |
| C5 | as_of 缺失/非法 | → None |
| C6 | 过期（PIT） | as_of 与注入 today 相差 6 个交易日 → None |
| C7 | 临界新鲜 | 相差 5 个交易日（含周末自然日拉长）→ 通过（返回 dict） |
| C8 | 正常读取 | 合法快照 → 返回 dict；`ntm_status` → reachable=True 且 verdict 正确 |
| C9 | is_fresh 边界 | 周末/跨周/未来日期（as_of>today → False）|

测试数据统一用 `tmp_path`/`monkeypatch` 构造临时快照文件，不得依赖真实 NTM 运行。

## 9. Step 7：回归与门禁（提交前必须全绿）

```powershell
<PY> -m pytest tests/test_national_team_overlay.py tests/test_ntm_client.py -q
<PY> -m pytest tests/test_strategy_plugin_contract.py tests/test_desk_supplement.py tests/test_astock_client.py tests/test_openapi_contract_v2.py -q
<PY> scripts/check_architecture.py --strict
```

要求：
- 新测试全绿；既有四个文件全绿（不许为过测试而改既有测试断言）；
- `check_architecture.py --strict` **exit 0**；
- OpenAPI 路径总数不变（本 Phase 不加 API）。

## 10. 交付证据（返回给我检查）

1. 改动的 5 个文件（含 diff 摘要）：`contracts.py`、`regimes/__init__.py`、`regimes/national_team_overlay_v1.py`、`configs/regimes/national_team_overlay_v1.yaml`、`integrations/ntm_client.py`、2 个新测试；
2. 上述三条命令的**完整输出**（含 PASS 行与 exit code）；
3. 若 Step 0 环境探测有异常，记录所用解释器路径。

**禁止事项**：不新增 API 路由；不改 paper/账本/写路径；不改 NTM 仓库；不引入新依赖；不改既有测试断言。
