"""T03 龙虎榜 PIT 回填：top_inst 断点续跑、空分区 fail-closed、生产库拒绝。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.application.lhb_reconcile import calendar_coverage, trace_to_manifest
from ab_screener.application.pit_backfill import PitBackfill, assert_copy_database
from ab_screener.data.migration_intents.aux_history_v2 import apply_aux_history
from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking
from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history


class FakeLhbPro:
    def __init__(self, *, empty_days: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self.empty_days = empty_days

    def trade_cal(self, **_kwargs):
        return pd.DataFrame(
            {
                "cal_date": ["20260810", "20260811", "20260812"],
                "is_open": [1, 1, 0],
            }
        )

    def top_inst(self, **kwargs):
        d = str(kwargs.get("trade_date") or kwargs.get("end_date") or "")
        self.calls.append(f"top_inst:{d}")
        if d in self.empty_days:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [d, d],
                "exalter": ["机构专用", "某证券深圳益田路营业部"],
                "side": ["BUY", "SELL"],
                "buy": [10.0, 0.0],
                "sell": [0.0, 2.0],
                "net_buy": [10.0, -2.0],
                "reason": ["日涨幅偏离值达到7%", "日涨幅偏离值达到7%"],
            }
        )


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "lhb_bf.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pit_history(conn)
        apply_aux_history(conn)
        apply_lhb_tracking(conn)
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_top_inst_backfill_and_resume(db: str):
    bf = PitBackfill(db, pro=FakeLhbPro())
    first = bf.run(["top_inst"], start="20260810", end="20260812")
    assert first["partitions_done"] == 2
    assert first["failed"] == 0
    second = bf.run(["top_inst"], start="20260810", end="20260812")
    assert second["skipped"] == 2 and second["partitions_done"] == 0
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM top_inst_history").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


def test_recent_done_partition_is_refetched_and_revision_is_appended(db: str):
    class RevisableFake(FakeLhbPro):
        buy_value = 10.0

        def top_inst(self, **kwargs):
            frame = super().top_inst(**kwargs)
            if not frame.empty and str(kwargs.get("trade_date")) == "20260810":
                frame.loc[0, "buy"] = self.buy_value
                frame.loc[0, "net_buy"] = self.buy_value
            return frame

    fake = RevisableFake()
    bf = PitBackfill(db, pro=fake)
    first = bf.run(["top_inst"], start="20260810", end="20260812")
    assert first["partitions_done"] == 2
    fake.buy_value = 11.0
    second = bf.run(["top_inst"], start="20260810", end="20260812")
    assert second["revised"] == 1
    assert second["revalidated"] == 1
    conn = sqlite3.connect(db)
    try:
        changed = conn.execute(
            "SELECT revision,json_extract(payload_json,'$.buy') FROM top_inst_history"
            " WHERE trade_date='20260810' AND exalter='机构专用' ORDER BY revision"
        ).fetchall()
        unchanged = conn.execute(
            "SELECT revision FROM top_inst_history"
            " WHERE trade_date='20260811' AND exalter='机构专用' ORDER BY revision"
        ).fetchall()
        assert changed == [(1, 10.0), (2, 11.0)]
        assert unchanged == [(1,)]
    finally:
        conn.close()


def test_empty_lhb_partition_is_not_valid_empty(db: str):
    bf = PitBackfill(db, pro=FakeLhbPro(empty_days=frozenset({"20260810"})))
    result = bf.run(["top_inst"], start="20260810", end="20260812")
    assert result["failed"] == 1
    assert "EMPTY_WITHOUT_PUBLISHED_FLAG" in result["per_dataset"]["top_inst"]["failed"][0]
    conn = sqlite3.connect(db)
    try:
        status = conn.execute(
            "SELECT status FROM pit_backfill_checkpoints"
            " WHERE dataset='top_inst' AND partition_key='20260810'"
        ).fetchone()
        assert status is None or status[0] != "done"
    finally:
        conn.close()


def test_calendar_coverage_ignores_weekend(db: str):
    bf = PitBackfill(db, pro=FakeLhbPro())
    bf.run(["top_inst"], start="20260810", end="20260812")
    conn = sqlite3.connect(db)
    try:
        cov = calendar_coverage(
            conn, dataset="top_inst", calendar_open_dates=["20260810", "20260811"]
        )
        assert cov["missing"] == []
        assert cov["pct"] == 100.0
        weekend = calendar_coverage(
            conn, dataset="top_inst", calendar_open_dates=["20260810", "20260811"]
        )
        assert "20260812" not in weekend["missing"]
    finally:
        conn.close()


def test_resume_in_progress_partition(db: str):
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO pit_backfill_checkpoints (dataset, partition_key, status,"
            " last_key, row_count, source_hash, updated_at) VALUES"
            " ('top_inst','20260810','in_progress','20260810',0,'','2026-08-12T00:00:00+08:00')"
        )
        conn.commit()
    finally:
        conn.close()
    bf = PitBackfill(db, pro=FakeLhbPro())
    result = bf.run(["top_inst"], start="20260810", end="20260812")
    assert result["partitions_done"] == 2


def test_trace_sample_to_raw_manifest(db: str, tmp_path: Path):
    days = [f"202607{i:02d}" for i in range(1, 22)]
    seats = [f"席位{i:02d}营业部" for i in range(20)]

    class WideFake(FakeLhbPro):
        def trade_cal(self, **_k):
            return pd.DataFrame({"cal_date": days, "is_open": [1] * len(days)})

        def top_inst(self, **kwargs):
            d = str(kwargs.get("trade_date") or kwargs.get("end_date") or "")
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 20,
                    "trade_date": [d] * 20,
                    "exalter": seats,
                    "side": ["BUY"] * 20,
                    "buy": [1.0] * 20,
                    "sell": [0.0] * 20,
                    "net_buy": [1.0] * 20,
                    "reason": ["日涨幅偏离值达到7%"] * 20,
                }
            )

    bf = PitBackfill(db, pro=WideFake())
    bf.run(["top_inst"], start=days[0], end=days[-1])
    conn = sqlite3.connect(db)
    try:
        report = trace_to_manifest(conn, n_days=20, n_seats=20)
        assert report["days_found"] >= 20
        assert report["seats_found"] >= 20
        assert report["pass"] is True
    finally:
        conn.close()


def test_production_db_path_rejected(tmp_path: Path):
    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()
    prod = fake_runtime / "stock_data.db"
    prod.write_bytes(b"x")
    with pytest.raises(ValueError, match="拒绝操作生产库"):
        assert_copy_database(prod)
    copy = tmp_path / "copy.db"
    copy.write_bytes(b"x")
    assert assert_copy_database(copy) == copy.resolve()
    with pytest.raises(ValueError, match="绝对路径"):
        assert_copy_database("relative.db")
