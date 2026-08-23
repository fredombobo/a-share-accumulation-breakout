"""信号管线（P4.2）：插件观察 → 不可变信号落库（幂等）。

管线调用六插件 `run_all_selection_plugins` 得到观察，逐个 `save_observation`
落库（同 strategy/snapshot/input hash 重跑幂等）。防守 overlay 不产生观察。

V2R-S 生产接线：
- A 池资格闸门：仅 `ACTIVE_FOR_A_POOL` 策略的观察可进入 A 池（EXPERIMENTAL 硬隔离）。
- fill 驱动 ENTERED：只有实际正数量 fill（`FillV2.filled=True, qty>0`）才触发。
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from ab_screener.data.signal_repository import (
    append_event,
    insert_observation,
)
from ab_screener.domain.signal_lifecycle import fill_qualifies_for_entered
from ab_screener.strategies.contracts import SignalObservation, StrategySpec
from ab_screener.strategies.registry import (
    resolve_selection,
    run_all_selection_plugins,
)

# A 池资格：只有已研究晋级并配置允许的版本才可进入可交易候选
A_POOL_REQUIRED_STATUS = "ACTIVE_FOR_A_POOL"

# 订单确认态：不是 fill，永远不得进入 ENTERED
ORDER_CONFIRM_STATUSES = ("CONFIRMED", "QUEUED")


class SignalPipelineError(RuntimeError):
    """管线错误（fail-closed）。"""


def is_a_pool_eligible(spec: StrategySpec) -> bool:
    """A 池资格闸门：research_status 必须为 ACTIVE_FOR_A_POOL。"""
    return spec.research_status == A_POOL_REQUIRED_STATUS


def a_pool_candidates(
    observations: list[SignalObservation],
) -> list[SignalObservation]:
    """EXPERIMENTAL 硬隔离：只放行 ACTIVE_FOR_A_POOL 策略的观察进入 A 池。

    未知策略 id → fail-closed（抛错，不静默放行）。
    """
    eligible: list[SignalObservation] = []
    for obs in observations:
        spec = resolve_selection(obs.strategy_definition_id)["spec"]
        if not is_a_pool_eligible(spec):
            continue
        eligible.append(obs)
    return eligible


def _fill_event_payload(fill: Any) -> dict[str, Any]:
    """fill → 事件 payload（v2 FillV2 优先；其余结构兜底）。"""
    if hasattr(fill, "to_dict"):
        return fill.to_dict()
    try:
        return asdict(fill)
    except (TypeError, ValueError):  # pragma: no cover - 兜底路径
        return {
            "ts_code": getattr(fill, "ts_code", ""),
            "side": getattr(fill, "side", ""),
            "trade_date": getattr(fill, "trade_date", ""),
            "filled": bool(getattr(fill, "filled", False)),
            "qty": int(getattr(fill, "qty", 0) or 0),
            "price_micro": getattr(fill, "price_micro", 0),
            "reason": str(getattr(fill, "reason", "")),
        }


def apply_fill_to_signal(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    fill: Any = None,
    order_state: str | None = None,
) -> dict[str, Any]:
    """成交驱动 ENTERED：只有实际正数量 fill 才进入 ENTERED。

    - CONFIRMED/QUEUED 只是订单确认，不是成交 → 不进入。
    - fill 为 None / 零成交 / 拒绝 / 过期 → 不进入（投影保持原状态）。
    - 正数量 fill → 追加 ENTERED 事件（actor=fill），投影推进到 ENTERED。
    - 非 ORDER_CREATED 状态收到 fill → SignalLifecycleError（fail-closed）。
    """
    if order_state in ORDER_CONFIRM_STATUSES:
        return {"entered": False, "reason": f"ORDER_NOT_FILLED:{order_state}"}
    if fill is None:
        return {"entered": False, "reason": "NO_FILL"}
    filled = bool(getattr(fill, "filled", False))
    qty = int(getattr(fill, "qty", 0) or 0)
    if not fill_qualifies_for_entered(filled=filled, qty=qty):
        reason = str(getattr(fill, "reason", "") or "ZERO_FILL")
        return {"entered": False, "reason": f"NO_QUALIFYING_FILL:{reason}"}
    event_id = append_event(
        conn,
        observation_id=observation_id,
        event_type="ENTERED",
        actor="fill",
        payload=_fill_event_payload(fill),
    )
    return {"entered": True, "event_id": event_id, "qty": qty}


def run_signal_pipeline(
    conn: sqlite3.Connection,
    *,
    bars: Any,
    ts_code: str,
    snapshot_id: str,
    input_hash: str,
    configs: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """执行全部 selection 插件并落库观察；返回 {saved, errors, a_pool_eligible}。"""
    results = run_all_selection_plugins(
        bars, ts_code=ts_code, snapshot_id=snapshot_id, input_hash=input_hash,
        configs=configs,
    )
    saved: list[str] = []
    saved_observations: list[SignalObservation] = []
    emitted_observations: list[SignalObservation] = []
    errors: dict[str, str] = {}
    for plugin_id, value in results.items():
        if isinstance(value, dict) and "error" in value:
            errors[plugin_id] = value["error"]
            continue
        for obs in value:
            if not isinstance(obs, SignalObservation):
                raise SignalPipelineError(
                    f"插件 {plugin_id} 返回非 SignalObservation: {type(obs).__name__}"
                )
            emitted_observations.append(obs)
            if insert_observation(conn, obs):
                saved.append(obs.observation_id)
                saved_observations.append(obs)
    # 返回资格是本次确定性计算结果，不能因 observation 已存在而在重放时消失。
    eligible = a_pool_candidates(emitted_observations)
    return {
        "plugins_run": list(results),
        "saved_observation_ids": saved,
        "saved_count": len(saved),
        "saved_observations": saved_observations,
        "a_pool_eligible_ids": [o.observation_id for o in eligible],
        "a_pool_eligible_count": len(eligible),
        "errors": errors,
    }
