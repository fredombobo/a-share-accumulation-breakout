"""状态机测试：合成突破序列 + JSON 序列化。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from logic_platform.structure.state_machine import StateMachine


def _make_df(n=100, close=None) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=n)
    if close is None:
        close = np.full(n, 10.0)
    df = pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "date": dates.strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close,
        "vol": np.full(n, 2e6),
    })
    return df


def _sig_no_box() -> dict:
    return {"is_breakout": False, "box_days": 0, "box_high": None,
            "box_low": None, "reasons": ["未找到合格横盘箱体"],
            "cond_hold": False, "box_amp": None, "breakout_date": None}


def _sig_box(vol_shrink=0.6) -> dict:
    return {"is_breakout": False, "box_days": 45, "box_high": 10.5,
            "box_low": 9.5, "box_amp": 0.06, "box_quality": 0.8,
            "box_start_idx": 10, "box_end_idx": 55,
            "reasons": ["箱体振幅 6%"], "cond_hold": False,
            "cond_box": True, "cond_flat": True,
            "vol_shrink_ratio": vol_shrink, "breakout_date": None}


def _sig_breakout() -> dict:
    return {"is_breakout": True, "box_days": 45, "box_high": 10.5,
            "box_low": 9.5, "box_amp": 0.06, "box_quality": 0.9,
            "box_start_idx": 10, "box_end_idx": 55,
            "reasons": ["放量突破"], "cond_hold": True,
            "cond_box": True, "cond_flat": True, "vol_shrink_ratio": 0.6,
            "breakout_date": "2026-04-15"}


def test_idle_when_no_box():
    df = _make_df()
    rec = StateMachine().evolve(df, _sig_no_box(), as_of="2026-04-30")
    assert rec.state == "IDLE"
    assert rec.box is None


def test_accumulation_when_box_ok():
    df = _make_df()
    rec = StateMachine().evolve(df, _sig_box(), as_of="2026-04-30")
    assert rec.state == "ACCUMULATION"
    assert rec.state_since == df["date"].iloc[10]  # box 起点
    assert rec.box and rec.box["days"] == 45


def test_breakout_when_signal_breakout():
    df = _make_df()
    rec = StateMachine().evolve(df, _sig_breakout(), as_of="2026-04-15")
    assert rec.state == "BREAKOUT"
    assert rec.is_breakout is True
    assert rec.state_since == "2026-04-15"


def test_follow_through_after_breakout():
    sig = _sig_breakout()
    sig["is_breakout"] = False
    sig["cond_hold"] = True
    df = _make_df()
    rec = StateMachine().evolve(df, sig, as_of="2026-04-30")
    assert rec.state == "FOLLOW_THROUGH"
    assert rec.state_since == "2026-04-15"


def test_fail_after_breakout_when_lost():
    sig = _sig_breakout()
    sig["is_breakout"] = False
    sig["cond_hold"] = False
    df = _make_df()
    rec = StateMachine().evolve(df, sig, as_of="2026-04-30")
    assert rec.state == "FAIL"


def test_to_json_serializable():
    df = _make_df()
    rec = StateMachine().evolve(df, _sig_box(), as_of="2026-04-30")
    j = rec.to_json()
    assert j["state"] == "ACCUMULATION"
    assert isinstance(j["transition_reasons"], list)
    assert "box" in j
    import json as _json
    _json.dumps(j, ensure_ascii=False)  # 可序列化
