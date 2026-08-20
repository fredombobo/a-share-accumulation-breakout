"""P4.3 信号观察测试：落库幂等、不可变、不同配置不覆盖。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending, registered_ids
from ab_screener.data.signal_repository import (
    SignalRepositoryError,
    projection_status,
    save_observation,
)
from ab_screener.strategies.contracts import SignalObservation, config_hash


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
    c = sqlite3.connect(str(tmp_path / "sig.db"))
    apply_pending(c)
    yield c
    c.close()


def test_save_observation_idempotent(conn):
    o1 = _obs()
    assert save_observation(conn, o1) == o1.observation_id
    # 同观察重跑幂等：行数不变
    assert save_observation(conn, o1) == o1.observation_id
    n = conn.execute("SELECT COUNT(*) FROM signal_observations").fetchone()[0]
    assert n == 1
    proj = projection_status(conn, o1.observation_id)
    assert proj["status"] == "OBSERVED" and proj["manual_exercise"] is False


def test_observation_immutable(conn):
    oid = save_observation(conn, _obs())
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "UPDATE signal_observations SET tradeable=0 WHERE observation_id=?", (oid,)
        )
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM signal_observations WHERE observation_id=?", (oid,))
    conn.rollback()


def test_different_config_does_not_overwrite(conn):
    o1 = _obs(config_hash=config_hash({"vol_ratio": 1.5}))
    o2 = _obs(config_hash=config_hash({"vol_ratio": 2.0}), input_hash="ih2")
    assert o1.observation_id != o2.observation_id
    save_observation(conn, o1)
    save_observation(conn, o2)
    n = conn.execute("SELECT COUNT(*) FROM signal_observations").fetchone()[0]
    assert n == 2


def test_missing_table_fail_closed(tmp_path: Path):
    empty = sqlite3.connect(str(tmp_path / "naked.db"))
    try:
        with pytest.raises(SignalRepositoryError, match="表不存在"):
            save_observation(empty, _obs())
    finally:
        empty.close()


def test_migration_registered():
    assert "v2:signals" in registered_ids()
