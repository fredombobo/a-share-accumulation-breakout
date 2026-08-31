"""T08 研究信号：披露后执行、归一化、硬否决、快照重算、policy 版本。"""
from __future__ import annotations

from copy import deepcopy

from ab_screener.application.lhb_signal_engine import load_policy, replay_signal, run_signal
from ab_screener.domain.lhb_signal import SignalInput, earliest_executable_at, policy_version_hash

CAL = ("20260810", "20260811", "20260812", "20260813")


def _inp(**over: object) -> SignalInput:
    base: dict[str, object] = {
        "ts_code": "000001.SZ",
        "disclose_date": "20260810",
        "disclose_at": "2026-08-10T16:00:00+08:00",
        "net_yuan": 50_000_000.0,
        "amount_yuan": 400_000_000.0,
        "adv20_yuan": 200_000_000.0,
        "purity": 0.8,
        "independent_actors": 3,
        "identity_confidence": 0.7,
        "identity_grade": "B",
        "turnover": 8.0,
        "data_complete": True,
        "next_bar_unfillable": False,
        "next_bar_suspended": False,
        "liquid": True,
        "crowded": False,
        "severe_abnormal": False,
        "calendar": CAL,
        "policy": load_policy(),
    }
    base.update(over)
    return SignalInput(**base)  # type: ignore[arg-type]


def test_earliest_executable_is_next_session_open():
    exec_at = earliest_executable_at("20260810", list(CAL))
    assert exec_at == "2026-08-11T09:30:00+08:00"
    out = run_signal(_inp())
    assert out["earliest_executable_at"] == exec_at
    assert out["earliest_executable_at"] > out["disclose_at"]
    assert out["research_only"] is True


def test_same_net_different_amount_changes_intensity():
    a = run_signal(_inp(amount_yuan=200_000_000.0))
    b = run_signal(_inp(amount_yuan=800_000_000.0))
    assert a["scores"]["net_over_amount"] != b["scores"]["net_over_amount"]
    assert a["scores"]["net_over_amount"] > b["scores"]["net_over_amount"]


def test_incomplete_data_cannot_confirm():
    out = run_signal(_inp(data_complete=False))
    assert out["status"] == "WATCH"
    assert "DATA_INCOMPLETE" in out["vetoes"]
    assert out["status"] not in ("CONFIRMED_FLOW", "RESEARCH_ENTRY")


def test_limit_up_or_suspended_is_no_chase():
    up = run_signal(_inp(next_bar_unfillable=True))
    sus = run_signal(_inp(next_bar_suspended=True))
    illiq = run_signal(_inp(liquid=False))
    assert up["status"] == "NO_CHASE"
    assert sus["status"] == "NO_CHASE"
    assert illiq["status"] == "NO_CHASE"


def test_recompute_from_snapshot_matches_and_ignores_new_policy():
    first = run_signal(_inp())
    replayed = replay_signal(first["feature_snapshot"])
    assert replayed["status"] == first["status"]
    assert replayed["scores"] == first["scores"]
    assert replayed["vetoes"] == first["vetoes"]
    mutated = deepcopy(first["feature_snapshot"])
    mutated["policy"] = {**mutated["policy"], "min_purity": 0.99, "version": "lhb-signal-v1-test"}
    # 快照仍用自己的 policy 重算；不读当前 yaml。
    again = replay_signal(first["feature_snapshot"])
    assert again["policy_hash"] == first["policy_hash"]
    changed = replay_signal(mutated)
    assert changed["policy_hash"] != first["policy_hash"]


def test_policy_hash_changes_with_threshold():
    p1 = load_policy()
    p2 = dict(p1)
    p2["min_net_over_amount"] = 0.99
    assert policy_version_hash(p1) != policy_version_hash(p2)
