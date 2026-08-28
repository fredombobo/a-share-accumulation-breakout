from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.instrument_repository import upsert_instrument
from ab_screener.data.migration_intents.instrument_history_v2 import apply_instrument_rules
from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history
from ab_screener.data.pit_writer import write_plain
from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.instrument import Instrument
from ab_screener.research.pit_reader import (
    PIT_READER_VERSION,
    ResearchPitError,
    build_research_pit_snapshot,
    latest_research_cutoff,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "research-pit.db"
    with sqlite3.connect(path) as conn:
        apply_pit_history(conn)
        apply_instrument_rules(conn)
        conn.execute(
            "CREATE TABLE daily ("
            "ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            "open REAL, high REAL, low REAL, close REAL, pre_close REAL,"
            "vol REAL, amount REAL, available_at TEXT,"
            "PRIMARY KEY(ts_code,trade_date))"
        )
    return path


def _instrument(
    path: Path,
    *,
    code: str = "000001.SZ",
    available_at: str = "2026-08-10T09:00:00+08:00",
    security_type: str = "stock",
    delist_date: str | None = None,
) -> None:
    with sqlite3.connect(path) as conn:
        upsert_instrument(
            conn,
            Instrument(
                ts_code=code,
                name=code,
                exchange="SZSE",
                security_type=security_type,
                list_date="19910101",
                delist_date=delist_date,
                source="tushare",
            ),
            available_at=available_at,
        )


def _payload(close: float) -> dict:
    return {
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "pre_close": close - 0.5,
        "vol": 100_000,
        "amount": 1_000_000,
    }


def _daily(
    path: Path,
    *,
    code: str = "000001.SZ",
    trade_date: str = "20260801",
    close: float = 10,
    available_at: str = "2026-08-10T16:00:00+08:00",
    update_projection: bool = True,
) -> None:
    payload = {"ts_code": code, "trade_date": trade_date, **_payload(close)}
    with sqlite3.connect(path) as conn:
        write_plain(
            conn,
            "daily",
            [payload],
            source="tushare",
            available_at=available_at,
            partition_key=trade_date,
        )
        if update_projection:
            conn.execute(
                "INSERT INTO daily(ts_code,trade_date,open,high,low,close,pre_close,vol,amount,available_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(ts_code,trade_date) DO UPDATE SET"
                " open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                " pre_close=excluded.pre_close,vol=excluded.vol,amount=excluded.amount,"
                " available_at=excluded.available_at",
                (
                    code,
                    trade_date,
                    payload["open"],
                    payload["high"],
                    payload["low"],
                    payload["close"],
                    payload["pre_close"],
                    payload["vol"],
                    payload["amount"],
                    available_at,
                ),
            )
            conn.commit()


def _snapshot(path: Path, decision_at: str):
    return build_research_pit_snapshot(
        path,
        study_start="20260801",
        study_end="20260803",
        max_codes=10,
        decision_at=decision_at,
        history_days=0,
    )


def test_cutoff_selects_old_then_new_daily_revision(db: Path) -> None:
    _instrument(db)
    _daily(db, close=10, available_at="2026-08-10T16:00:00+08:00")
    _daily(db, close=11, available_at="2026-08-12T09:00:00+08:00")

    old = _snapshot(db, "2026-08-11T10:00:00+08:00")
    new = _snapshot(db, "2026-08-12T10:00:00+08:00")

    assert old.daily.iloc[0]["close"] == 10
    assert old.daily.iloc[0]["revision"] == 1
    assert new.daily.iloc[0]["close"] == 11
    assert new.daily.iloc[0]["revision"] == 2
    assert old.dataset_fingerprint != new.dataset_fingerprint


def test_universe_uses_lifecycle_history_not_current_projection(db: Path) -> None:
    _instrument(db, available_at="2026-08-10T09:00:00+08:00")
    _instrument(
        db,
        available_at="2026-08-12T09:00:00+08:00",
        security_type="other",
    )
    _daily(db, available_at="2026-08-10T16:00:00+08:00")

    snapshot = _snapshot(db, "2026-08-11T10:00:00+08:00")

    assert snapshot.universe == ("000001.SZ",)
    assert snapshot.version == PIT_READER_VERSION


def test_projection_row_without_visible_pit_history_fails_closed(db: Path) -> None:
    _instrument(db)
    payload = _payload(10)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily(ts_code,trade_date,open,high,low,close,pre_close,vol,amount,available_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "000001.SZ",
                "20260801",
                payload["open"],
                payload["high"],
                payload["low"],
                payload["close"],
                payload["pre_close"],
                payload["vol"],
                payload["amount"],
                "2026-08-10T16:00:00+08:00",
            ),
        )

    with pytest.raises(ResearchPitError, match="缺日线"):
        _snapshot(db, "2026-08-11T10:00:00+08:00")


def test_tampered_content_hash_fails_closed(db: Path) -> None:
    _instrument(db)
    payload = _payload(10)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily_history"
            "(ts_code,trade_date,revision,available_at,source,content_hash,payload_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                "000001.SZ",
                "20260801",
                1,
                "2026-08-10T16:00:00+08:00",
                "tushare",
                "tampered",
                canonical_json(payload),
            ),
        )

    with pytest.raises(ResearchPitError, match="content_hash"):
        _snapshot(db, "2026-08-11T10:00:00+08:00")


def test_snapshot_identity_and_reads_are_deterministic(db: Path) -> None:
    _instrument(db)
    for day in range(1, 4):
        _daily(db, trade_date=f"2026080{day}", close=10 + day)

    first = _snapshot(db, "2026-08-11T10:00:00+08:00")
    second = _snapshot(db, "2026-08-11T10:00:00+08:00")

    assert first.identity() == second.identity()
    assert first.distinct_dates() == ["20260801", "20260802", "20260803"]
    assert len(first.load_daily(ts_codes=["000001.SZ"], start="20260802")) == 2
    with pytest.raises(ResearchPitError, match="冻结宇宙之外"):
        first.load_daily(ts_codes=["000002.SZ"])


def test_latest_cutoff_reads_both_history_tables(db: Path) -> None:
    _instrument(db, available_at="2026-08-10T09:00:00+08:00")
    _daily(db, available_at="2026-08-12T16:00:00+08:00")

    assert latest_research_cutoff(db) == "2026-08-12T16:00:00+08:00"


def test_suspension_zero_quote_is_preserved_as_non_tradeable_bar(db: Path) -> None:
    _instrument(db)
    with sqlite3.connect(db) as conn:
        write_plain(
            conn,
            "daily",
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260801",
                    "open": 0,
                    "high": 0,
                    "low": 0,
                    "close": 10,
                    "pre_close": 10,
                    "vol": 0,
                    "amount": 0,
                }
            ],
            source="tushare",
            available_at="2026-08-10T16:00:00+08:00",
            partition_key="20260801",
        )

    snapshot = _snapshot(db, "2026-08-11T10:00:00+08:00")

    row = snapshot.daily.iloc[0]
    assert row["open"] == 0
    assert row["vol"] == 0
    assert row["close"] == 10


def test_snapshot_binds_benchmark_revision_at_same_cutoff(db: Path) -> None:
    _instrument(db)
    _daily(db, code="000001.SZ", close=10, available_at="2026-08-10T16:00:00+08:00")
    _daily(db, code="000300.SH", close=4000, available_at="2026-08-10T16:00:00+08:00")
    _daily(db, code="000300.SH", close=4100, available_at="2026-08-12T09:00:00+08:00")

    old = build_research_pit_snapshot(
        db,
        study_start="20260801",
        study_end="20260803",
        max_codes=10,
        decision_at="2026-08-11T10:00:00+08:00",
        history_days=0,
        benchmark_code="000300.SH",
    )
    new = build_research_pit_snapshot(
        db,
        study_start="20260801",
        study_end="20260803",
        max_codes=10,
        decision_at="2026-08-12T10:00:00+08:00",
        history_days=0,
        benchmark_code="000300.SH",
    )

    assert old.load_benchmark().iloc[0]["close"] == 4000
    assert new.load_benchmark().iloc[0]["close"] == 4100
    assert old.identity()["benchmark_code"] == "000300.SH"
    assert len(str(old.identity()["benchmark_sha256"])) == 64
    assert old.dataset_fingerprint != new.dataset_fingerprint


def test_requested_benchmark_projection_without_pit_history_fails_closed(db: Path) -> None:
    _instrument(db)
    _daily(db, code="000001.SZ", close=10)
    payload = _payload(4000)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily(ts_code,trade_date,open,high,low,close,pre_close,vol,amount,available_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "000300.SH",
                "20260801",
                payload["open"],
                payload["high"],
                payload["low"],
                payload["close"],
                payload["pre_close"],
                payload["vol"],
                payload["amount"],
                "2026-08-10T16:00:00+08:00",
            ),
        )

    with pytest.raises(ResearchPitError, match="000300.SH:20260801"):
        build_research_pit_snapshot(
            db,
            study_start="20260801",
            study_end="20260803",
            max_codes=10,
            decision_at="2026-08-11T10:00:00+08:00",
            history_days=0,
            benchmark_code="000300.SH",
        )
