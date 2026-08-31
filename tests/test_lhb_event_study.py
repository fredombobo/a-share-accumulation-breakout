"""T09 事件研究：匹配对照与原始收益并列，涨停不按开盘成交。"""
from __future__ import annotations

from ab_screener.research.lhb_event_study import event_study, tag_matched


def _bars() -> dict[str, dict[str, dict[str, float]]]:
    return {
        "000001.SZ": {
            "20260811": {"open": 10.0, "close": 10.2, "low": 9.9, "limit_up": 11.0},
            "20260812": {"open": 10.2, "close": 10.5, "low": 10.1, "limit_up": 11.2},
            "20260813": {"open": 10.5, "close": 10.4, "low": 10.3, "limit_up": 11.5},
        },
        "000002.SZ": {
            "20260811": {"open": 20.0, "close": 19.8, "low": 19.7, "limit_up": 22.0},
            "20260812": {"open": 19.8, "close": 19.5, "low": 19.4, "limit_up": 21.8},
            "20260813": {"open": 19.5, "close": 19.6, "low": 19.3, "limit_up": 21.5},
        },
    }


CAL = ["20260810", "20260811", "20260812", "20260813", "20260814"]


def test_event_study_shows_matched_and_unmatched():
    events = [
        {"ts_code": "000001.SZ", "disclose_date": "20260810", "matched": True},
        {"ts_code": "000002.SZ", "disclose_date": "20260810", "matched": False},
    ]
    out = event_study(events, bars=_bars(), calendar=CAL)
    h1 = out["horizons"]["1"]
    assert h1["raw"]["n"] == 2
    assert h1["matched_control"]["n"] == 1
    assert h1["unmatched_raw"]["n"] == 1
    assert out["shows_matched_and_unmatched"] is True
    assert out["fill_stats"]["FILLED"] == 2


def test_tag_matched_by_reason_date_mv():
    rows = [
        {"ts_code": "a", "reason_code": "PCT_DEV_UP_1D", "disclose_date": "20260810", "float_mv_yuan": 1e10},
        {"ts_code": "b", "reason_code": "PCT_DEV_UP_1D", "disclose_date": "20260810", "float_mv_yuan": 1.1e10},
        {"ts_code": "c", "reason_code": "AMPLITUDE_1D", "disclose_date": "20260810", "float_mv_yuan": 1e10},
    ]
    tagged = tag_matched(rows)
    by_code = {r["ts_code"]: r["matched"] for r in tagged}
    assert by_code["a"] is True
    assert by_code["b"] is True
    assert by_code["c"] is False
