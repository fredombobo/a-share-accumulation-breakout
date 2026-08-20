"""容量模型（P3.3）：ADV20 容量、预计退出天数、多档账户规模。"""
from __future__ import annotations

from typing import Any


class CapacityError(ValueError):
    """容量输入非法（fail-closed）。"""


def daily_capacity_yuan(
    adv20_yuan: float,
    participation_bps: int = 500,
    cap_pct_of_adv: float = 0.05,
) -> float:
    """单日最大成交额容量 = ADV20 × 参与率（上限按 ADV 比例封顶）。"""
    if adv20_yuan <= 0:
        raise CapacityError("ADV20 必须为正")
    if not 0 < participation_bps <= 10_000:
        raise CapacityError("参与率必须在 (0, 10000] bps")
    capacity = adv20_yuan * participation_bps / 10_000.0
    cap = adv20_yuan * cap_pct_of_adv
    return min(capacity, cap)


def expected_exit_days(position_value_yuan: float, adv20_yuan: float, participation_bps: int = 500) -> float:
    """预计退出天数 = 持仓市值 / 每日可成交额。"""
    per_day = daily_capacity_yuan(adv20_yuan, participation_bps)
    if per_day <= 0:
        raise CapacityError("日容量为零")
    return position_value_yuan / per_day


def account_scale_scenarios(
    adv20_yuan: float,
    position_weights: list[float],
    participation_bps: int = 500,
) -> list[dict[str, Any]]:
    """多档账户规模：{position_value, days_to_exit, capacity_ok}。"""
    capacity = daily_capacity_yuan(adv20_yuan, participation_bps)
    out = []
    for weight in position_weights:
        if not 0 < weight <= 1:
            raise CapacityError("持仓权重必须在 (0,1]")
        position_value = capacity * weight
        out.append(
            {
                "weight": weight,
                "position_value": round(position_value, 2),
                "days_to_exit": round(expected_exit_days(position_value, adv20_yuan, participation_bps), 2),
                "capacity_ok": expected_exit_days(position_value, adv20_yuan, participation_bps) <= 10.0,
            }
        )
    return out
