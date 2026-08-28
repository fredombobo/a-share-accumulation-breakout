from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.data.benchmark_pit_sync import (
    BenchmarkPitSyncError,
    missing_benchmark_pit_dates,
    sync_benchmark_pit_history,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.local_store import LocalStore


class Provider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def index_daily(self, **_kwargs):
        self.calls += 1
        return self.frame.copy()


def _frame(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": "000300.SH",
            "trade_date": dates,
            "open": 4000.0,
            "high": 4050.0,
            "low": 3950.0,
            "close": 4020.0,
            "pre_close": 3990.0,
            "change": 30.0,
            "pct_chg": 0.75,
            "vol": 100_000.0,
            "amount": 1_000_000.0,
        }
    )


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "benchmark.db"
    store = LocalStore(path)
    with sqlite3.connect(path) as conn:
        apply_pending(conn)
    legacy = _frame(["20260825", "20260826"])
    legacy["available_at"] = "2026-08-27T09:00:00+08:00"
    legacy["source"] = "legacy_sync"
    store.upsert_daily(legacy)
    return path


def test_dry_run_is_read_only_and_does_not_call_provider(db: Path) -> None:
    provider = Provider(_frame(["20260825", "20260826"]))

    result = sync_benchmark_pit_history(db, provider, apply=False)

    assert result["status"] == "PLANNED"
    assert result["target_dates"] == 2
    assert provider.calls == 0
    assert missing_benchmark_pit_dates(db) == ["20260825", "20260826"]


def test_apply_preserves_canonical_then_records_verified_provider_revision(db: Path) -> None:
    provider = Provider(_frame(["20260825", "20260826"]))

    result = sync_benchmark_pit_history(
        db,
        provider,
        apply=True,
        available_at="2026-08-28T20:30:00+08:00",
    )

    assert result["status"] == "COMPLETED"
    assert result["applied_dates"] == 2
    assert result["recovered_revisions"] == 2
    assert result["provider_revisions"] == 2
    assert missing_benchmark_pit_dates(db) == []
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT trade_date,revision,source,available_at FROM daily_history "
            "WHERE ts_code='000300.SH' ORDER BY trade_date,revision"
        ).fetchall()
    assert rows == [
        ("20260825", 1, "canonical_recovery:legacy_sync", "2026-08-27T09:00:00+08:00"),
        ("20260825", 2, "tushare_index_daily_pit_repair", "2026-08-28T20:30:00+08:00"),
        ("20260826", 1, "canonical_recovery:legacy_sync", "2026-08-27T09:00:00+08:00"),
        ("20260826", 2, "tushare_index_daily_pit_repair", "2026-08-28T20:30:00+08:00"),
    ]

    replay = sync_benchmark_pit_history(db, provider, apply=True)
    assert replay["status"] == "NOOP"
    assert provider.calls == 1


def test_provider_gap_fails_before_any_write(db: Path) -> None:
    provider = Provider(_frame(["20260825"]))

    with pytest.raises(BenchmarkPitSyncError, match="未覆盖全部目标日期"):
        sync_benchmark_pit_history(db, provider, apply=True)

    assert missing_benchmark_pit_dates(db) == ["20260825", "20260826"]
