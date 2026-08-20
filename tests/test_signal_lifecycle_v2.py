"""P4.3 信号生命周期测试：状态机、ENTERED 只由 fill 触发、人工练习单。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.signal_repository import (
    append_event,
    projection_status,
    save_observation,
)
from ab_screener.domain.signal_lifecycle import SignalLifecycleError, transition
from ab_screener.strategies.contracts import SignalObservation


def _obs(**over) -> SignalObservation:
    base = {
        "strategy_definition_id": "accumulation_breakout_v1",
        "strategy_hash": "sh1", "input_hash": "ih1", "snapshot_id": "snap1",
        "ts_code": "000001.SZ", "signal_date": "20260810", "config_hash": "ch1",
        "payload": {"box_days": 76}, "explanation": "放量突破",
        "tradeable": True, "entry_definition_id": "NEXT_TRADABLE_OPEN_EXECUTION_V1",
    }
    base.update(over)
    return SignalObservation(**base)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "lc.db"))
    apply_pending(c)
    yield c
    c.close()


def _save(conn) -> str:
    return save_observation(conn, _obs())


def test_domain_state_machine():
    transition("OBSERVED", "QUALIFIED")
    transition("QUALIFIED", "TRADEABLE")
    transition("TRADEABLE", "ORDER_CREATED")
    transition("ORDER_CREATED", "ENTERED")
    with pytest.raises(SignalLifecycleError, match="非法状态转移"):
        transition("OBSERVED", "ENTERED")   # 不可跳跃
    with pytest.raises(SignalLifecycleError, match="非法状态转移"):
        transition("ENTERED", "ORDER_CREATED")  # 终态


def test_projection_advances_with_events(conn):
    oid = _save(conn)
    append_event(conn, observation_id=oid, event_type="QUALIFIED", actor="pipeline")
    append_event(conn, observation_id=oid, event_type="TRADEABLE", actor="gate")
    assert projection_status(conn, oid)["status"] == "TRADEABLE"
    append_event(conn, observation_id=oid, event_type="ORDER_CREATED", actor="order")
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"


def test_entered_only_via_fill(conn):
    oid = _save(conn)
    # 直接从 OBSERVED 跳 ENTERED 拒绝
    with pytest.raises(SignalLifecycleError, match="ENTERED"):
        append_event(conn, observation_id=oid, event_type="ENTERED", actor="fill")
    # 合法路径后由 fill 触发 ENTERED
    for event in ("QUALIFIED", "TRADEABLE", "ORDER_CREATED"):
        append_event(conn, observation_id=oid, event_type=event, actor="system")
    append_event(conn, observation_id=oid, event_type="ENTERED", actor="fill")
    assert projection_status(conn, oid)["status"] == "ENTERED"


def test_manual_exercise_flag(conn):
    oid = _save(conn)
    append_event(conn, observation_id=oid, event_type="QUALIFIED",
                 actor="user", manual_exercise=True)
    proj = projection_status(conn, oid)
    assert proj["manual_exercise"] is True


def test_unknown_observation_fail_closed(conn):
    with pytest.raises(SignalLifecycleError, match="观察不存在"):
        append_event(conn, observation_id="NOPE", event_type="QUALIFIED", actor="x")
