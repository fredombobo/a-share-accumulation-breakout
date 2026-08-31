"""T01 龙虎榜迁移：空库/已有 v2 副本双跑幂等、checksum 冻结、append-only、唯一键。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from ab_screener.data import migration_registry as mr
from ab_screener.data.migration_intents.lhb_tracking_v2 import (
    APPEND_ONLY_TABLES,
    LHB_PIT_HISTORY_TABLES,
    apply_lhb_tracking,
)
from ab_screener.domain.lhb_contracts import LhbEventKey, require_available_at

PUBLISHED_MIGRATION_CHECKSUMS = {
    "v2:aux_history": "db22e80e7cdd9c98",
    "v2:corporate_actions": "7e04e70bb86ef50b",
    "v2:execution_lineage": "1b35fa03e104c000",
    "v2:instrument_rules": "159d954eeb354661",
    "v2:operations": "8a52183189d9e002",
    "v2:pit_history": "5d679ee799f7e4b3",
    "v2:portfolio_risk": "289b762bd8dc9d59",
    "v2:research_governance": "928d12bf7acadb0c",
    "v2:review": "9ea13e5a14fb3e08",
    "v2:scan_profiles": "4060a78dc1df41e0",
    "v2:signals": "020a6fe663ada0a1",
}

_TS = "2026-08-10T16:00:00+08:00"


def _schema_snapshot(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        )
    ]
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        for name in names
    }


def _apply_except(conn: sqlite3.Connection, skip: frozenset[str]) -> list[str]:
    mr._ensure_intents()
    mr.ensure_table(conn)
    applied = set(mr.applied_migrations(conn))
    order: list[str] = []
    visited: set[str] = set()

    def visit(mid: str) -> None:
        if mid in visited or mid in applied:
            return
        if mid in skip:
            visited.add(mid)
            return
        visited.add(mid)
        for dep in mr._REGISTRY[mid]["depends_on"]:
            visit(dep)
        order.append(mid)

    for mid in mr.registered_ids():
        visit(mid)
    done: list[str] = []
    for mid in order:
        t0 = time.monotonic()
        mr._REGISTRY[mid]["apply"](conn)
        conn.execute(
            "INSERT INTO schema_migrations_v2 (migration_id, checksum, applied_at, duration_ms)"
            " VALUES (?,?,datetime('now'),?)",
            (mid, mr.migration_checksum(mid), int((time.monotonic() - t0) * 1000)),
        )
        conn.commit()
        done.append(mid)
    return done


def _seed_top_list(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO top_list_history (ts_code, trade_date, revision, available_at, source,"
        " content_hash, payload_json) VALUES (?,?,?,?,?,?,?)",
        (
            "000001.SZ",
            "20260810",
            1,
            _TS,
            "tushare",
            "0123456789abcdef",
            '{"reason":"日涨幅偏离值达到7%"}',
        ),
    )
    conn.commit()


def test_migration_registered():
    assert "v2:lhb_tracking" in mr.registered_ids()


def test_published_migration_checksums_unchanged():
    current = {
        mid: mr.migration_checksum(mid)
        for mid in PUBLISHED_MIGRATION_CHECKSUMS
    }
    assert current == PUBLISHED_MIGRATION_CHECKSUMS


def test_empty_db_apply_twice_idempotent(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        first = mr.apply_pending(conn)
        assert "v2:lhb_tracking" in first
        assert "v2:lhb_ops" in first
        snap = _schema_snapshot(conn)
        counts = _table_counts(conn)
        second = mr.apply_pending(conn)
        assert second == []
        assert _schema_snapshot(conn) == snap
        assert _table_counts(conn) == counts
        for table in APPEND_ONLY_TABLES:
            assert counts[table] == 0
    finally:
        conn.close()


def test_existing_v2_schema_apply_twice_does_not_change_data(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "v2copy.db"))
    try:
        applied = _apply_except(conn, frozenset({"v2:lhb_tracking"}))
        assert "v2:lhb_tracking" not in applied
        assert "v2:aux_history" in applied
        _seed_top_list(conn)
        before = _table_counts(conn)
        assert before["top_list_history"] == 1
        first = mr.apply_pending(conn)
        assert first == ["v2:lhb_tracking"]
        second = mr.apply_pending(conn)
        assert second == []
        after = _table_counts(conn)
        assert after["top_list_history"] == 1
        assert after["schema_migrations_v2"] == before["schema_migrations_v2"] + 1
        for table, n in before.items():
            if table == "schema_migrations_v2":
                continue
            assert after[table] == n
        for table in APPEND_ONLY_TABLES:
            assert table in after
    finally:
        conn.close()


def test_history_and_mapping_tables_are_append_only(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "ao.db"))
    try:
        apply_lhb_tracking(conn)
        conn.execute(
            "INSERT INTO top_inst_history (ts_code, trade_date, exalter, reason, side,"
            " revision, available_at, source, content_hash, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "000001.SZ",
                "20260810",
                "机构专用",
                "日涨幅偏离值达到7%",
                "BUY",
                1,
                _TS,
                "tushare",
                "hashhashhashhash",
                "{}",
            ),
        )
        conn.execute(
            "INSERT INTO seat_alias (alias_raw, seat_id, revision, valid_from, source,"
            " available_at, ingested_at, content_hash) VALUES (?,?,?,?,?,?,?,?)",
            ("机构专用", "seat-1", 1, "20200101", "manual", _TS, _TS, "hashhashhashhash"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE top_inst_history SET source='x'")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM top_inst_history")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE seat_alias SET seat_id='seat-2'")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM seat_alias")
        conn.rollback()
        conn.execute(
            "INSERT INTO seat_alias (alias_raw, seat_id, revision, valid_from, source,"
            " available_at, ingested_at, content_hash) VALUES (?,?,?,?,?,?,?,?)",
            ("机构专用", "seat-1", 2, "20240101", "manual", _TS, _TS, "hashhashhashhash"),
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM seat_alias").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_unique_keys_cover_multi_reason_and_windows(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "keys.db"))
    try:
        apply_lhb_tracking(conn)
        keys = [
            LhbEventKey(
                exchange="SZ",
                ts_code="000001.SZ",
                window_code="D1",
                reason_code="PCT_DEV_UP_1D",
                disclose_date="20260810",
            ),
            LhbEventKey(
                exchange="SZ",
                ts_code="000001.SZ",
                window_code="D1",
                reason_code="TURNOVER_1D",
                disclose_date="20260810",
            ),
            LhbEventKey(
                exchange="SZ",
                ts_code="000001.SZ",
                window_code="D3",
                reason_code="PCT_DEV_UP_3D",
                disclose_date="20260810",
            ),
        ]
        for key in keys:
            conn.execute(
                "INSERT INTO lhb_event (event_id, revision, exchange, ts_code, window_code,"
                " reason_code, reason_raw, reason_catalog_version, disclose_date, source,"
                " source_status, available_at, ingested_at, content_hash, payload_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key.event_id,
                    1,
                    key.exchange,
                    key.ts_code,
                    key.window_code,
                    key.reason_code,
                    key.reason_code,
                    "v1",
                    key.disclose_date,
                    "tushare",
                    "COMPLETE",
                    require_available_at(_TS),
                    _TS,
                    "hashhashhashhash",
                    "{}",
                ),
            )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM lhb_event").fetchone()[0]
        assert n == 3
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lhb_event (event_id, revision, exchange, ts_code, window_code,"
                " reason_code, reason_raw, reason_catalog_version, disclose_date, source,"
                " source_status, available_at, ingested_at, content_hash, payload_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    keys[0].event_id,
                    1,
                    keys[0].exchange,
                    keys[0].ts_code,
                    keys[0].window_code,
                    keys[0].reason_code,
                    keys[0].reason_code,
                    "v1",
                    keys[0].disclose_date,
                    "tushare",
                    "COMPLETE",
                    _TS,
                    _TS,
                    "hashhashhashhash",
                    "{}",
                ),
            )
        conn.rollback()
    finally:
        conn.close()


def test_dual_board_sql_amounts_once_and_two_ranks(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "dual.db"))
    try:
        apply_lhb_tracking(conn)
        conn.execute(
            "INSERT INTO lhb_seat_trade (event_id, seat_raw, revision, buy_amount_fen,"
            " sell_amount_fen, net_amount_fen, source, available_at, ingested_at, content_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("evt1", "机构专用", 1, 8_000_000, 2_000_000, 6_000_000, "tushare", _TS, _TS, "h"),
        )
        conn.execute(
            "INSERT INTO lhb_seat_rank (event_id, seat_raw, side, rank_no, revision, source,"
            " available_at, ingested_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ("evt1", "机构专用", "BUY", 1, 1, "tushare", _TS, _TS, "h"),
        )
        conn.execute(
            "INSERT INTO lhb_seat_rank (event_id, seat_raw, side, rank_no, revision, source,"
            " available_at, ingested_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ("evt1", "机构专用", "SELL", 4, 1, "tushare", _TS, _TS, "h"),
        )
        conn.commit()
        trades = conn.execute("SELECT COUNT(*), SUM(net_amount_fen) FROM lhb_seat_trade").fetchone()
        ranks = conn.execute("SELECT COUNT(*) FROM lhb_seat_rank").fetchone()[0]
        assert trades == (1, 6_000_000)
        assert ranks == 2
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lhb_seat_trade (event_id, seat_raw, revision, buy_amount_fen,"
                " sell_amount_fen, net_amount_fen, source, available_at, ingested_at, content_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("evt1", "机构专用", 1, 1, 0, 2, "tushare", _TS, _TS, "h"),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lhb_seat_trade (event_id, seat_raw, revision, buy_amount_fen,"
                " sell_amount_fen, net_amount_fen, source, available_at, ingested_at, content_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("evt2", "机构专用", 1, -1, 0, -1, "tushare", _TS, _TS, "h"),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lhb_ingest_manifests (manifest_id, dataset, partition_key, source,"
                " revision, source_status, row_count, content_sha256, available_at, ingested_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("m1", "top_inst", "20260810", "tushare", 1, "SUCCESS", 0, "x", _TS, _TS),
            )
        conn.rollback()
    finally:
        conn.close()


def test_signal_row_cannot_leave_research_only(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "sig.db"))
    try:
        apply_lhb_tracking(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lhb_signal_observation (observation_id, revision, ts_code,"
                " signal_date, disclose_at, earliest_executable_at, status, research_only,"
                " scores_json, policy_version, data_version, identity_version, source,"
                " available_at, ingested_at, content_hash) VALUES"
                " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "obs1",
                    1,
                    "000001.SZ",
                    "20260810",
                    _TS,
                    "2026-08-11T09:30:00+08:00",
                    "WATCH",
                    0,
                    "{}",
                    "p1",
                    "d1",
                    "i1",
                    "research",
                    _TS,
                    _TS,
                    "h",
                ),
            )
        conn.rollback()
    finally:
        conn.close()


def test_pit_history_key_columns_registered():
    assert LHB_PIT_HISTORY_TABLES["top_inst_history"] == [
        "ts_code",
        "trade_date",
        "exalter",
        "reason",
        "side",
    ]
    assert LHB_PIT_HISTORY_TABLES["hm_list_history"] == ["hm_name", "list_date"]
