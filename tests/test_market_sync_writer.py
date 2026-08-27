"""Canonical/PIT market sync writes are atomic, append-only, and idempotent."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.data.market_sync_writer import (
    market_partition_pit_status,
    reconcile_market_partition,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.local_store import LocalStore, sync_from_tushare


def _daily(close: float, *, trade_date: str = "20260826") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": [trade_date],
            "open": [10.0],
            "high": [10.5],
            "low": [9.9],
            "close": [close],
            "pre_close": [9.9],
            "change": [close - 9.9],
            "pct_chg": [(close / 9.9 - 1) * 100],
            "vol": [12345.67],
            "amount": [123456.78],
        }
    )


@pytest.fixture
def market_db(tmp_path: Path) -> Path:
    path = tmp_path / "market.db"
    LocalStore(path)
    with closing(sqlite3.connect(path)) as conn:
        apply_pending(conn)
    return path


def test_new_partition_writes_projection_history_and_manifest(market_db: Path) -> None:
    result = reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-26T16:20:00+08:00",
    )

    with sqlite3.connect(market_db) as conn:
        canonical = conn.execute(
            "SELECT close,revision,source,available_at FROM daily "
            "WHERE ts_code='600000.SH' AND trade_date='20260826'"
        ).fetchone()
        history = conn.execute(
            "SELECT revision,source,payload_json FROM daily_history "
            "WHERE ts_code='600000.SH' AND trade_date='20260826'"
        ).fetchall()
        manifest = conn.execute(
            "SELECT dataset,partition_key,row_count FROM raw_ingest_manifests"
        ).fetchone()

    assert result["appended_revisions"] == 1
    assert result["canonical_updated"] == 1
    assert canonical == (10.2, 1, "tushare", "2026-08-26T16:20:00+08:00")
    assert len(history) == 1
    assert history[0][:2] == (1, "tushare")
    assert json.loads(history[0][2])["close"] == 10.2
    assert manifest == ("daily", "20260826", 1)


def test_identical_replay_does_not_create_a_revision_or_manifest(market_db: Path) -> None:
    first = reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-26T16:20:00+08:00",
    )
    second = reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-27T09:00:00+08:00",
    )

    with sqlite3.connect(market_db) as conn:
        history_count = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
        manifest_count = conn.execute(
            "SELECT COUNT(*) FROM raw_ingest_manifests"
        ).fetchone()[0]

    assert first["manifest_ids"]
    assert second["appended_revisions"] == 0
    assert second["canonical_updated"] == 0
    assert second["unchanged"] == 1
    assert second["manifest_ids"] == []
    assert history_count == 1
    assert manifest_count == 1


def test_recovered_value_gets_verified_source_provenance_even_when_unchanged(
    market_db: Path,
) -> None:
    store = LocalStore(market_db)
    old = _daily(10.2)
    old["available_at"] = "2026-08-26T16:10:00+08:00"
    old["source"] = "legacy_sync"
    store.upsert_daily(old)

    result = reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-27T09:00:00+08:00",
    )

    with sqlite3.connect(market_db) as conn:
        rows = conn.execute(
            "SELECT revision,source FROM daily_history ORDER BY revision"
        ).fetchall()
        current = conn.execute("SELECT revision,source FROM daily").fetchone()

    assert result["recovered_revisions"] == 1
    assert result["appended_revisions"] == 1
    assert rows == [(1, "canonical_recovery:legacy_sync"), (2, "tushare")]
    assert current == (2, "tushare")


def test_untracked_canonical_value_is_preserved_before_source_revision(
    market_db: Path,
) -> None:
    store = LocalStore(market_db)
    old = _daily(10.1)
    old["available_at"] = "2026-08-26T16:10:00+08:00"
    old["source"] = "legacy_sync"
    store.upsert_daily(old)

    result = reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-27T09:00:00+08:00",
    )

    with sqlite3.connect(market_db) as conn:
        rows = conn.execute(
            "SELECT revision,available_at,source,payload_json FROM daily_history "
            "WHERE ts_code='600000.SH' AND trade_date='20260826' ORDER BY revision"
        ).fetchall()
        current = conn.execute(
            "SELECT close,revision,source FROM daily "
            "WHERE ts_code='600000.SH' AND trade_date='20260826'"
        ).fetchone()

    assert result["recovered_revisions"] == 1
    assert result["appended_revisions"] == 1
    assert [row[0] for row in rows] == [1, 2]
    assert rows[0][1] == "2026-08-26T16:10:00+08:00"
    assert rows[0][2] == "canonical_recovery:legacy_sync"
    assert json.loads(rows[0][3])["close"] == 10.1
    assert rows[1][2] == "tushare"
    assert json.loads(rows[1][3])["close"] == 10.2
    assert current == (10.2, 2, "tushare")


@pytest.mark.parametrize(
    ("dataset", "frame", "value_column"),
    [
        (
            "daily_basic",
            pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": ["20260826"],
                    "close": [10.2],
                    "pe": [6.2],
                    "pb": [0.7],
                    "total_mv": [123.0],
                }
            ),
            "pe",
        ),
        (
            "moneyflow",
            pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": ["20260826"],
                    "buy_lg_amount": [100.0],
                    "sell_lg_amount": [80.0],
                    "net_mf_amount": [20.0],
                }
            ),
            "net_mf_amount",
        ),
    ],
)
def test_supported_market_datasets_share_the_same_atomic_path(
    market_db: Path,
    dataset: str,
    frame: pd.DataFrame,
    value_column: str,
) -> None:
    reconcile_market_partition(
        market_db,
        dataset,
        frame,
        trade_date="20260826",
        available_at="2026-08-26T16:20:00+08:00",
    )

    with sqlite3.connect(market_db) as conn:
        canonical = conn.execute(
            f"SELECT {value_column} FROM {dataset} WHERE ts_code=? AND trade_date=?",
            ("600000.SH", "20260826"),
        ).fetchone()
        history = conn.execute(
            f"SELECT payload_json FROM {dataset}_history "
            "WHERE ts_code=? AND trade_date=?",
            ("600000.SH", "20260826"),
        ).fetchone()

    assert canonical is not None
    assert history is not None
    assert json.loads(history[0])[value_column] == canonical[0]


def test_manifest_failure_rolls_back_history_and_projection(market_db: Path) -> None:
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            "CREATE TRIGGER reject_manifest BEFORE INSERT ON raw_ingest_manifests "
            "BEGIN SELECT RAISE(ABORT, 'manifest rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="manifest rejected"):
        reconcile_market_partition(
            market_db,
            "daily",
            _daily(10.2),
            trade_date="20260826",
            available_at="2026-08-26T16:20:00+08:00",
        )

    with sqlite3.connect(market_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0] == 0


def test_mixed_or_duplicate_business_keys_are_rejected_before_writes(
    market_db: Path,
) -> None:
    mixed = pd.concat([_daily(10.2), _daily(10.3, trade_date="20260825")])
    with pytest.raises(ValueError, match="包含其它交易日"):
        reconcile_market_partition(
            market_db,
            "daily",
            mixed,
            trade_date="20260826",
            available_at="2026-08-26T16:20:00+08:00",
        )

    duplicate = pd.concat([_daily(10.2), _daily(10.3)], ignore_index=True)
    with pytest.raises(ValueError, match="标的代码缺失或重复"):
        reconcile_market_partition(
            market_db,
            "daily",
            duplicate,
            trade_date="20260826",
            available_at="2026-08-26T16:20:00+08:00",
        )

    with sqlite3.connect(market_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0] == 0


def test_partition_status_fails_closed_until_projection_and_history_match(
    market_db: Path,
) -> None:
    LocalStore(market_db).upsert_daily(_daily(10.2))
    before = market_partition_pit_status(market_db, "daily", "20260826")
    assert before["passed"] is False
    assert before["missing_history"] == 1

    reconcile_market_partition(
        market_db,
        "daily",
        _daily(10.2),
        trade_date="20260826",
        available_at="2026-08-27T09:00:00+08:00",
    )
    after = market_partition_pit_status(market_db, "daily", "20260826")
    assert after["passed"] is True
    assert after["canonical_rows"] == after["history_rows"] == 1


def test_incremental_sync_reconciles_recent_source_without_duplicate_revisions(
    market_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ab_screener.local_store as store_module
    import tushare_init

    dates = ["20260824", "20260825", "20260826"]

    class FakePro:
        def trade_cal(self, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame({"cal_date": dates, "is_open": [1, 1, 1]})

        def stock_basic(self, *, list_status: str, **_kwargs: object) -> pd.DataFrame:
            if list_status == "D":
                return pd.DataFrame(
                    columns=["ts_code", "name", "list_date", "delist_date"]
                )
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "symbol": ["600000"],
                    "name": ["浦发银行"],
                    "area": ["上海"],
                    "industry": ["银行"],
                    "market": ["主板"],
                    "list_date": ["19991110"],
                }
            )

        def daily(self, *, trade_date: str) -> pd.DataFrame:
            return _daily(10.2, trade_date=trade_date)

        def daily_basic(self, *, trade_date: str, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": [trade_date],
                    "close": [10.2],
                    "pe": [6.2],
                    "pb": [0.7],
                    "total_mv": [123.0],
                }
            )

        def moneyflow(self, *, trade_date: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": [trade_date],
                    "net_mf_amount": [20.0],
                }
            )

        def index_daily(self, **_kwargs: object) -> pd.DataFrame:
            rows = []
            for trade_date in dates:
                row = _daily(4_000.0, trade_date=trade_date).iloc[0].to_dict()
                row["ts_code"] = "000300.SH"
                row["open"] = 3_990.0
                row["high"] = 4_010.0
                row["low"] = 3_980.0
                rows.append(row)
            return pd.DataFrame(rows)

    monkeypatch.setattr(store_module, "_DB_PATH", market_db)
    monkeypatch.setattr(tushare_init, "pro", FakePro())

    first = sync_from_tushare(days_back=3, verbose=False)
    second = sync_from_tushare(days_back=3, verbose=False)

    assert first["daily_dates"] == dates
    assert first["daily_checked_dates"] == dates
    assert first["failed_daily_dates"] == []
    assert second["daily_dates"] == []
    assert second["daily_checked_dates"] == dates
    assert second["rows"]["daily"] == 0
    assert second["rows"]["daily_basic"] == 0
    assert second["rows"]["moneyflow"] == 0
    assert second["appended_revisions"] == {
        "daily": 0,
        "daily_basic": 0,
        "benchmark": 0,
        "moneyflow": 0,
    }
    with sqlite3.connect(market_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM daily_basic_history").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM moneyflow_history").fetchone()[0] == 3
