"""P4.3 信号 outcome 测试：净收益计算、NULL 不填 0、修订追加、horizon 校验。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.application.signal_outcomes import (
    OutcomeError,
    compute_outcome,
    outcomes_for_observation,
    record_outcome,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.signal_repository import save_observation
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
    c = sqlite3.connect(str(tmp_path / "oc.db"))
    apply_pending(c)
    yield c
    c.close()


def _save(conn) -> str:
    return save_observation(conn, _obs())


def test_compute_outcome_net_of_costs():
    # 买入 10.0 元卖出 10.5 元，双边成本 20bp → 净 +4.8%
    r = compute_outcome(entry_price_micro=10_000_000, exit_price_micro=10_500_000,
                        cost_rate=0.001, benchmark_excess=0.002)
    assert r["net_return"] == pytest.approx(0.048, abs=1e-8)
    assert r["benchmark_excess"] == 0.002


def test_unfillable_returns_null_not_zero(conn):
    oid = _save(conn)
    record_outcome(conn, observation_id=oid, horizon_days=5, status="UNFILLABLE")
    record_outcome(conn, observation_id=oid, horizon_days=10, status="EXPIRED")
    rows = outcomes_for_observation(conn, oid)
    assert all(r["net_return"] is None for r in rows)
    # 不允许给 UNFILLABLE 填 0/数值
    with pytest.raises(OutcomeError, match="NULL"):
        record_outcome(conn, observation_id=oid, horizon_days=20, status="UNFILLABLE",
                       net_return=0.0)


def test_revision_append_not_overwrite(conn):
    oid = _save(conn)
    record_outcome(conn, observation_id=oid, horizon_days=5, status="MATURED",
                   entry_price_micro=10_000_000, exit_price_micro=10_200_000,
                   net_return=0.018, available_at="2026-08-17T16:00:00+08:00")
    # 修订（修正）追加新版本
    record_outcome(conn, observation_id=oid, horizon_days=5, status="MATURED",
                   entry_price_micro=10_000_000, exit_price_micro=10_250_000,
                   net_return=0.023, available_at="2026-08-18T16:00:00+08:00")
    rows = outcomes_for_observation(conn, oid)
    assert len(rows) == 2
    assert [r["revision"] for r in rows] == [1, 2]
    assert rows[-1]["net_return"] == pytest.approx(0.023, abs=1e-8)


def test_horizon_and_matured_validation(conn):
    oid = _save(conn)
    with pytest.raises(OutcomeError, match="horizon"):
        record_outcome(conn, observation_id=oid, horizon_days=7, status="MATURED",
                       entry_price_micro=1, exit_price_micro=2, net_return=0.0)
    with pytest.raises(OutcomeError, match="入场"):
        record_outcome(conn, observation_id=oid, horizon_days=5, status="MATURED",
                       entry_price_micro=None, exit_price_micro=2, net_return=0.0)
