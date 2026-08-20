"""状态机（docs §4.2）：对单票最新 as_of 输出可解释状态记录。

每日（每个 as_of）输出 state / state_since / transition_reason[]，
可序列化 JSON 供前端 K 线 markArea / markPoint。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import pandas as pd

from logic_platform.structure.adapters_signals import (
    STATE_ACCUMULATION,
    STATE_BREAKOUT,
    STATE_FAIL,
    STATE_FOLLOW_THROUGH,
    STATE_TIGHTENING,
    box_date_range,
    is_tightening,
    map_signal_to_state,
)


@dataclass
class StateRecord:
    state: str
    as_of: str                      # YYYY-MM-DD（最新交易日）
    state_since: str | None = None  # 进入该状态的首日（YYYY-MM-DD）
    transition_reasons: list[str] = field(default_factory=list)
    is_breakout: bool = False
    breakout_date: str | None = None
    tightening: bool = False
    box: dict | None = None         # high/low/mid/amp/days/start_date/end_date

    def to_json(self) -> dict:
        d = asdict(self)
        d["state"] = self.state
        return d

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, default=str)


class StateMachine:
    """单票状态机：evolve(df, sig, as_of) -> StateRecord。"""

    def evolve(
        self, df: pd.DataFrame, sig: dict, as_of: str | None = None
    ) -> StateRecord:
        state, reasons = map_signal_to_state(sig)
        if as_of is None:
            as_of = str(df["date"].iloc[-1]) if df is not None and len(df) else ""

        box = self._box_payload(df, sig)
        is_breakout = bool(sig.get("is_breakout"))
        breakout_date = sig.get("breakout_date")

        state_since = self._state_since(state, box, breakout_date)
        tightening = False
        if state == STATE_ACCUMULATION:
            tightening = bool(is_tightening(df, sig.get("box_amp")))  # np.bool_ → bool
            if tightening:
                reasons.append("近5日量能再缩且振幅收窄（TIGHTENING 特征）")

        final_state = STATE_TIGHTENING if (tightening and state == STATE_ACCUMULATION) else state
        if final_state != state:
            reasons = [f"由 {state} 升级：{reasons[0]}" if reasons else "量价双缩"]

        return StateRecord(
            state=final_state,
            as_of=as_of,
            state_since=state_since,
            transition_reasons=reasons,
            is_breakout=is_breakout,
            breakout_date=breakout_date,
            tightening=tightening,
            box=box,
        )

    # ── 内部 ──

    @staticmethod
    def _box_payload(df: pd.DataFrame, sig: dict) -> dict | None:
        if not sig or sig.get("box_high") is None or sig.get("box_low") is None:
            return None
        start_date, end_date = box_date_range(df, sig)
        high = float(sig["box_high"])
        low = float(sig["box_low"])
        return {
            "high": round(high, 3),
            "low": round(low, 3),
            "mid": round((high + low) / 2.0, 3),
            "amp": float(sig["box_amp"]) if sig.get("box_amp") is not None else None,
            "days": int(sig.get("box_days") or 0),
            "quality": float(sig.get("box_quality") or 0),
            "start_date": start_date,
            "end_date": end_date,
        }

    @staticmethod
    def _state_since(state: str, box: dict | None, breakout_date: str | None) -> str | None:
        if state in (STATE_BREAKOUT, STATE_FOLLOW_THROUGH, STATE_FAIL):
            return breakout_date
        if state in (STATE_ACCUMULATION, STATE_TIGHTENING) and box:
            return box.get("start_date")
        return None
