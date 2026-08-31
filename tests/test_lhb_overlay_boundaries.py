"""T12 overlay：关闭无变化；打开只加解释字段，不改分数/仓位/订单。"""
from __future__ import annotations

from ab_screener.application.lhb_overlay import (
    attach_lhb_overlay,
    overlay_changed_pool,
    overlay_enabled,
)
from ab_screener.application.lhb_signal_engine import load_policy


def _pool() -> list[dict]:
    return [
        {"ts_code": "000001.SZ", "score": 88.0, "rank": 1, "pool": "A", "tradeable": True, "position": 0.1},
        {"ts_code": "000002.SZ", "score": 70.0, "rank": 2, "pool": "B", "tradeable": False, "position": 0.0},
    ]


def test_overlay_default_off():
    policy = load_policy()
    assert policy["overlay_enabled"] is False
    assert overlay_enabled(policy) is False
    pool = _pool()
    out = attach_lhb_overlay(pool, {"000001.SZ": {"status": "WATCH"}}, enabled=False)
    assert overlay_changed_pool(pool, out) is False
    assert "lhb_research" not in out[0]


def test_overlay_on_research_blocked_only_explains():
    pool = _pool()
    sig = {
        "000001.SZ": {
            "status": "RESEARCH_ENTRY",
            "vetoes": [],
            "policy_version": "lhb-signal-v1",
            "earliest_executable_at": "2026-08-11T09:30:00+08:00",
        }
    }
    out = attach_lhb_overlay(pool, sig, enabled=True, research_status="RESEARCH_BLOCKED")
    assert overlay_changed_pool(pool, out) is False
    assert out[0]["score"] == 88.0
    assert out[0]["rank"] == 1
    assert out[0]["position"] == 0.1
    assert out[0]["lhb_research"]["research_only"] is True
    assert out[0]["lhb_research"]["generates_orders"] is False
    assert out[0]["lhb_research"]["does_not_change_pool"] is True
    assert out[0].get("order") is None
    assert "lhb_research" in out[1]
    assert out[1]["lhb_research"]["signal"] is None
