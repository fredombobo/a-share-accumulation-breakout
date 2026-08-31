"""T11 独立 LHB DAG：幂等、租约、上游失败阻断 confirmed、5 日 soak。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ab_screener.application.lhb_daily import (
    FROZEN_MAIN_DAG_STEPS,
    LHB_DAG_STEPS,
    LHB_MODE,
    clamp_signal_status,
    emit_quality_alert,
    run_lhb_day,
    try_lease,
)
from ab_screener.data.migration_intents.lhb_ops_v2 import apply_lhb_ops
from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking
from ab_screener.data.migration_intents.operations_v2 import apply_operations
from ab_screener.operations.dag import DAG_STEPS


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_operations(conn)
        apply_lhb_tracking(conn)
        apply_lhb_ops(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def test_main_dag_steps_frozen():
    assert DAG_STEPS == FROZEN_MAIN_DAG_STEPS
    assert "lhb_ingest" not in DAG_STEPS
    for step in LHB_DAG_STEPS:
        assert step not in DAG_STEPS


def test_idempotent_rerun_same_input_hash(tmp_path: Path):
    db = _db(tmp_path)
    calls: list[str] = []

    def ingest(**_: object) -> None:
        calls.append("ingest")

    first = run_lhb_day(str(db), "20260810", holder="h1", fns={"lhb_ingest": ingest})
    second = run_lhb_day(str(db), "20260810", holder="h1", fns={"lhb_ingest": ingest})
    assert first["status"] == "COMPLETED"
    assert second["results"]["lhb_ingest"].get("idempotent") or second["results"]["lhb_ingest"].get(
        "resumed"
    )
    assert calls.count("ingest") == 1


def test_lease_contention(tmp_path: Path):
    db = _db(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        assert try_lease(conn, trade_date="20260810", holder="alpha") is True
        assert try_lease(conn, trade_date="20260810", holder="beta") is False
    finally:
        conn.close()


def test_fetch_failed_blocks_confirmed_but_quality_alert(tmp_path: Path):
    assert clamp_signal_status("RESEARCH_ENTRY", "FETCH_FAILED") == "WATCH"
    assert clamp_signal_status("CONFIRMED_FLOW", "DEGRADED") == "WATCH"
    assert clamp_signal_status("WATCH", "COMPLETE") == "WATCH"
    db = _db(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        alert = emit_quality_alert(conn, trade_date="20260810", source_status="FETCH_FAILED")
        assert alert is not None
        again = emit_quality_alert(conn, trade_date="20260810", source_status="FETCH_FAILED")
        assert again is not None
        assert again["deduped"] is True
    finally:
        conn.close()


def test_five_day_soak(tmp_path: Path):
    db = _db(tmp_path)
    days = ["20260810", "20260811", "20260812", "20260813", "20260814"]
    for day in days:
        out = run_lhb_day(str(db), day, holder="soak")
        assert out["status"] == "COMPLETED"
        assert out["main_dag_untouched"] is True
    conn = sqlite3.connect(str(db))
    try:
        n_runs = conn.execute("SELECT COUNT(*) FROM dag_runs WHERE mode=?", (LHB_MODE,)).fetchone()[0]
        n_ok = conn.execute(
            "SELECT COUNT(*) FROM dag_step_runs WHERE status='SUCCESS'"
        ).fetchone()[0]
        leases = conn.execute("SELECT COUNT(*) FROM dag_leases WHERE lease_id LIKE 'lhb:%'").fetchone()[0]
        assert n_runs == 5
        assert n_ok >= 5 * len(LHB_DAG_STEPS)
        assert leases == 5
    finally:
        conn.close()
