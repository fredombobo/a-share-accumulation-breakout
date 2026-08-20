"""P1.1 PIT 仓库测试：decision_at 语义、修订切换、fail-closed、时区。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history
from ab_screener.data.pit_repository import PitRepository
from ab_screener.data.pit_writer import write_plain
from ab_screener.domain.data_point import (
    PitRecord,
    normalize_ts,
    record_valid_at,
    select_asof,
)


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "pit.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pit_history(conn)
    finally:
        conn.close()
    return str(path)


def _write(db: str, dataset: str, rows: list[dict], *, available_at: str, key: str) -> None:
    conn = sqlite3.connect(db)
    try:
        write_plain(
            conn, dataset, rows, source="tushare", available_at=available_at, partition_key=key
        )
    finally:
        conn.close()


def test_asof_before_available_returns_none(db: str):
    repo = PitRepository(db)
    assert (
        repo.read_asof(
            "daily",
            {"ts_code": "000001.SZ", "trade_date": "20260810"},
            "2026-08-09T00:00:00+08:00",
        )
        is None
    )


def test_asof_switches_revision_old_to_new(db: str):
    row = {"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}
    _write(db, "daily", [row], available_at="2026-08-10T16:00:00+08:00", key="20260810")
    # 修订 1 后又修正 close
    row2 = {"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.5}
    _write(db, "daily", [row2], available_at="2026-08-11T09:30:00+08:00", key="20260810")

    repo = PitRepository(db)
    old = repo.read_asof(
        "daily", {"ts_code": "000001.SZ", "trade_date": "20260810"},
        "2026-08-10T20:00:00+08:00",
    )
    assert old is not None and old["revision"] == 1 and old["payload"]["close"] == 10.0
    new = repo.read_asof(
        "daily", {"ts_code": "000001.SZ", "trade_date": "20260810"},
        "2026-08-11T10:00:00+08:00",
    )
    assert new is not None and new["revision"] == 2 and new["payload"]["close"] == 10.5
    # read_all：两个修订升序
    all_ = repo.read_all("daily", {"ts_code": "000001.SZ", "trade_date": "20260810"})
    assert [r["revision"] for r in all_] == [1, 2]


def test_missing_fields_fail_closed():
    with pytest.raises(ValueError, match="available_at"):
        PitRecord(business_key={"a": "1"}, revision=1, available_at="", source="tushare")
    with pytest.raises(ValueError, match="source"):
        PitRecord(business_key={"a": "1"}, revision=1, available_at="2026-08-01T00:00:00+08:00", source="")
    with pytest.raises(ValueError, match="revision"):
        PitRecord(business_key={"a": "1"}, revision=0, available_at="2026-08-01T00:00:00+08:00", source="tushare")
    with pytest.raises(ValueError, match="业务键"):
        PitRecord(business_key={}, revision=1, available_at="2026-08-01T00:00:00+08:00", source="tushare")


def test_timezone_normalization_to_plus08():
    assert normalize_ts("2026-08-01T09:00:00") == "2026-08-01T09:00:00+08:00"
    assert normalize_ts("2026-08-01T01:00:00Z") == "2026-08-01T09:00:00+08:00"


def test_record_valid_at_and_select_asof():
    r1 = PitRecord(business_key={"ts_code": "1"}, revision=1,
                   available_at="2026-08-01T00:00:00+08:00", source="tushare", payload={"close": 1})
    r2 = PitRecord(business_key={"ts_code": "1"}, revision=2,
                   available_at="2026-08-03T00:00:00+08:00", source="tushare", payload={"close": 2})
    assert record_valid_at(r1, "2026-08-02T00:00:00+08:00") is True
    assert select_asof([r1, r2], "2026-08-02T00:00:00+08:00").revision == 1
    assert select_asof([r1, r2], "2026-08-04T00:00:00+08:00").revision == 2
    assert select_asof([r1, r2], "2026-07-01T00:00:00+08:00") is None


def test_migration_registered_in_registry():
    from ab_screener.data.migration_registry import registered_ids

    assert "v2:pit_history" in registered_ids()


def test_unknown_dataset_fail_closed(db: str):
    repo = PitRepository(db)
    with pytest.raises(ValueError, match="未知 PIT 数据集"):
        repo.read_asof("nope", {"a": "1"}, "2026-08-01T00:00:00+08:00")


def test_manifest_rows_recorded(db: str):
    _write(
        db, "daily",
        [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}],
        available_at="2026-08-10T16:00:00+08:00", key="20260810",
    )
    repo = PitRepository(db)
    manifests = repo.manifest_rows(dataset="daily")
    assert len(manifests) == 1
    assert manifests[0]["dataset"] == "daily"
    assert manifests[0]["row_count"] == 1
    assert len(manifests[0]["content_sha256"]) == 64
