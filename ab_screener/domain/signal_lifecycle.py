"""信号生命周期（P4.3）：状态机 + 事件 + 投影。

状态机：OBSERVED → QUALIFIED → WATCHING|TRADEABLE → ORDER_CREATED → ENTERED。
- ENTERED 只由 fill 触发（不是 confirm）。
- 同 strategy/profile/entry/snapshot/input hash 重跑幂等；不同配置不互相覆盖。
- 人工练习单保存 `manual_exercise=true`，禁止按同代码最近信号猜测关联。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_STATES = (
    "OBSERVED", "QUALIFIED", "WATCHING", "TRADEABLE",
    "ORDER_CREATED", "ENTERED", "RETIRED",
)

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "OBSERVED": {"QUALIFIED", "RETIRED"},
    "QUALIFIED": {"WATCHING", "TRADEABLE", "RETIRED"},
    "WATCHING": {"TRADEABLE", "RETIRED"},
    "TRADEABLE": {"ORDER_CREATED", "WATCHING", "RETIRED"},
    "ORDER_CREATED": {"ENTERED", "RETIRED"},   # ENTERED 只由 fill 触发
    "ENTERED": set(),
    "RETIRED": set(),
}


class SignalLifecycleError(ValueError):
    """非法状态转移（fail-closed）。"""


@dataclass(frozen=True)
class SignalEvent:
    observation_id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    occurred_at: str


def transition(from_state: str, to_state: str) -> None:
    """校验状态转移；非法转移抛错。"""
    if from_state not in VALID_STATES or to_state not in VALID_STATES:
        raise SignalLifecycleError(f"未知状态: {from_state}→{to_state}")
    if to_state not in _ALLOWED_TRANSITIONS.get(from_state, set()):
        raise SignalLifecycleError(
            f"非法状态转移: {from_state}→{to_state}"
        )


def entered_requires_fill(from_state: str, event: str) -> None:
    """ENTERED 只能由 fill 事件触发。"""
    if from_state == "ORDER_CREATED" and event == "ENTERED":
        return
    if event == "ENTERED":
        raise SignalLifecycleError("ENTERED 只能由实际 fill 触发")
