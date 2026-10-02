"""筹码前向 PIT 捕获：真实可用时点、append-only、分页防截断、严格 PIT 就绪统计（离线）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ab_screener.data.chip_pit_capture import (
    SOURCE,
    ChipCaptureError,
    capture_chip_snapshot,
    chip_pit_readiness,
    fetch_cyq_partition,
)
from ab_screener.data.migration_intents.aux_history_v2 import apply_aux_history
from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history

TZ = ZoneInfo("Asia/Shanghai")
DAY = "20260924"
NEXT_DAY = "20260925"
CODES = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "830799.BJ"]


def _clock(text: str):
    return lambda: datetime.fromisoformat(text).replace(tzinfo=TZ)


class FakePro:
    """cyq_perf 按 offset/limit 切片；可模拟忽略 offset 的网关。"""

    def __init__(self, rows: list[dict], *, ignore_offset: bool = False) -> None:
        self.rows = rows
        self.ignore_offset = ignore_offset
        self.calls: list[tuple[int, int]] = []

    def cyq_perf(self, *, trade_date: str, offset: int = 0, limit: int = 5000):
        self.calls.append((offset, limit))
        start = 0 if self.ignore_offset else offset
        page = [r for r in self.rows if r["trade_date"] == trade_date][start:start + limit]
        return pd.DataFrame(page)


def _cyq_row(code: str, day: str = DAY, winner: float = 50.0) -> dict:
    return {
        "ts_code": code,
        "trade_date": day,
        "his_low": 1.0,
        "his_high": 20.0,
        "cost_5pct": 5.0,
        "cost_95pct": 15.0,
        "weight_avg": 10.0,
        "winner_rate": winner,
    }


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "stock_copy.db"
    conn = sqlite3.connect(path)
    apply_pit_history(conn)
    apply_aux_history(conn)
    conn.execute("CREATE TABLE daily (ts_code TEXT, trade_date TEXT, close REAL)")
    conn.executemany(
        "INSERT INTO daily VALUES (?,?,?)", [(code, DAY, 10.0) for code in CODES]
    )
    conn.execute(
        "CREATE TABLE trade_cal (cal_date TEXT PRIMARY KEY, is_open INTEGER NOT NULL,"
        " source TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO trade_cal VALUES (?,?,?,?)",
        [(d, 1, "tushare", "2026-09-01T00:00:00+08:00") for d in ("20260923", DAY, NEXT_DAY)],
    )
    conn.commit()
    conn.close()
    return path


def _history(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT ts_code, trade_date, revision, available_at, source FROM cyq_history"
            " ORDER BY ts_code, revision"
        ).fetchall()


def _manifests(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT dataset, partition_key, source, available_at, row_count FROM raw_ingest_manifests"
        ).fetchall()


def test_plan_mode_does_not_touch_provider_or_database(db: Path) -> None:
    result = capture_chip_snapshot(db, None, clock=_clock("2026-09-24T18:30:00"))
    assert result["status"] == "PLANNED"
    assert result["trade_date"] == DAY
    assert result["expected_codes"] == 4  # 沪深 4 只；北交所不进覆盖率分母
    assert _history(db) == []


def test_apply_stamps_actual_capture_time_and_registers_manifest(db: Path) -> None:
    pro = FakePro([_cyq_row(c) for c in CODES])
    result = capture_chip_snapshot(db, pro, apply=True, clock=_clock("2026-09-24T18:30:00"))

    assert result["status"] == "COMPLETED"
    assert result["coverage"] == 1.0
    assert result["appended_rows"] == 5
    assert result["bj_rows"] == 1
    rows = _history(db)
    assert {r[2] for r in rows} == {1}
    assert {r[3] for r in rows} == {"2026-09-24T18:30:00+08:00"}
    assert {r[4] for r in rows} == {SOURCE}
    assert _manifests(db) == [("cyq", DAY, SOURCE, "2026-09-24T18:30:00+08:00", 5)]


def test_rerun_with_same_content_is_noop(db: Path) -> None:
    pro = FakePro([_cyq_row(c) for c in CODES])
    capture_chip_snapshot(db, pro, apply=True, clock=_clock("2026-09-24T18:30:00"))
    again = capture_chip_snapshot(db, pro, apply=True, clock=_clock("2026-09-24T21:00:00"))

    assert again["status"] == "NOOP"
    assert again["appended_rows"] == 0
    assert len(_history(db)) == 5
    assert len(_manifests(db)) == 1
    # 原始可用时点保留，不被较晚的重跑覆盖
    assert {r[3] for r in _history(db)} == {"2026-09-24T18:30:00+08:00"}


def test_vendor_revision_appends_only_changed_rows(db: Path) -> None:
    capture_chip_snapshot(
        db, FakePro([_cyq_row(c) for c in CODES]), apply=True, clock=_clock("2026-09-24T18:30:00")
    )
    revised = [_cyq_row(c, winner=77.0 if c == "600519.SH" else 50.0) for c in CODES]
    result = capture_chip_snapshot(
        db, FakePro(revised), apply=True, clock=_clock("2026-09-25T08:00:00")
    )

    assert result["status"] == "COMPLETED"
    assert result["appended_rows"] == 1
    assert result["revised_rows"] == 1
    history = [r for r in _history(db) if r[0] == "600519.SH"]
    assert [(r[2], r[3]) for r in history] == [
        (1, "2026-09-24T18:30:00+08:00"),
        (2, "2026-09-25T08:00:00+08:00"),
    ]


def test_pagination_collects_every_page() -> None:
    rows = [_cyq_row(c) for c in CODES]
    pro = FakePro(rows)
    fetched, pages = fetch_cyq_partition(pro, DAY, page_size=2, max_pages=4)
    assert pages == 3
    assert sorted(r["ts_code"] for r in fetched) == sorted(CODES)
    assert pro.calls == [(0, 2), (2, 2), (4, 2)]


def test_gateway_ignoring_offset_is_refused_before_any_write(db: Path) -> None:
    pro = FakePro([_cyq_row(c) for c in CODES], ignore_offset=True)
    with pytest.raises(ChipCaptureError, match="offset"):
        capture_chip_snapshot(
            db, pro, apply=True, page_size=2, clock=_clock("2026-09-24T18:30:00")
        )
    assert _history(db) == []
    assert _manifests(db) == []


def test_all_full_pages_up_to_limit_is_treated_as_truncation() -> None:
    pro = FakePro([_cyq_row(c) for c in CODES[:4]])
    with pytest.raises(ChipCaptureError, match="满页"):
        fetch_cyq_partition(pro, DAY, page_size=2, max_pages=2)


def test_rows_for_another_date_are_refused() -> None:
    class WrongDatePro:
        def cyq_perf(self, **_kwargs):
            return pd.DataFrame([_cyq_row("000001.SZ", day="20260923")])

    with pytest.raises(ChipCaptureError, match="非目标日期"):
        fetch_cyq_partition(WrongDatePro(), DAY)


def test_capture_before_close_is_refused(db: Path) -> None:
    with pytest.raises(ChipCaptureError, match="尚未收盘"):
        capture_chip_snapshot(
            db, FakePro([]), apply=True, clock=_clock("2026-09-24T14:59:00")
        )


def test_partial_capture_writes_what_arrived_and_rerun_fills_the_gap(db: Path) -> None:
    first = capture_chip_snapshot(
        db,
        FakePro([_cyq_row(c) for c in CODES[:2]]),
        apply=True,
        clock=_clock("2026-09-24T18:30:00"),
    )
    assert first["status"] == "PARTIAL"
    assert first["coverage"] == 0.5
    assert first["missing_codes"] == 2

    second = capture_chip_snapshot(
        db, FakePro([_cyq_row(c) for c in CODES]), apply=True, clock=_clock("2026-09-24T20:00:00")
    )
    assert second["status"] == "COMPLETED"
    assert second["appended_rows"] == 3
    stamps = {r[0]: r[3] for r in _history(db)}
    assert stamps["000001.SZ"] == "2026-09-24T18:30:00+08:00"
    assert stamps["600519.SH"] == "2026-09-24T20:00:00+08:00"


def test_empty_vendor_response_writes_nothing(db: Path) -> None:
    result = capture_chip_snapshot(db, FakePro([]), apply=True, clock=_clock("2026-09-24T18:30:00"))
    assert result["status"] == "EMPTY"
    assert _history(db) == []
    assert _manifests(db) == []


def test_missing_daily_partition_is_refused(db: Path) -> None:
    with pytest.raises(ChipCaptureError, match="daily 缺少"):
        capture_chip_snapshot(db, None, trade_date="20260923", clock=_clock("2026-09-24T18:30:00"))


def test_missing_schema_is_refused_without_migrating(tmp_path: Path) -> None:
    path = tmp_path / "bare.db"
    sqlite3.connect(path).close()
    with pytest.raises(ChipCaptureError, match="不做迁移"):
        capture_chip_snapshot(path, None)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_readiness_counts_only_captures_before_next_open(db: Path) -> None:
    capture_chip_snapshot(
        db, FakePro([_cyq_row(c) for c in CODES]), apply=True, clock=_clock("2026-09-24T18:30:00")
    )
    with sqlite3.connect(db) as conn:
        # 旧式批量入库：20260923 的数据到 09-24 下午才入库 → 不可用于 09-24 开盘决策
        conn.execute(
            "INSERT INTO raw_ingest_manifests VALUES (?,?,?,?,?,?,?,?)",
            ("legacy", "cyq", "20260923", "tushare", "2026-09-24T16:00:00+08:00", 5, "x", "x"),
        )
    report = chip_pit_readiness(db)

    assert report["partitions_with_manifest"] == 2
    assert report["strict_usable_dates"] == 1
    assert report["late_only_dates"] == 1
    assert report["first_usable"] == DAY
    assert report["usable"][0]["entry_cutoff"] == "2026-09-25T09:15:00+08:00"
    assert report["usable"][0]["calendar_exact"] is True


def test_readiness_falls_back_to_next_calendar_day_without_calendar(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM trade_cal WHERE cal_date=?", (NEXT_DAY,))
    capture_chip_snapshot(
        db, FakePro([_cyq_row(c) for c in CODES]), apply=True, clock=_clock("2026-09-24T18:30:00")
    )
    report = chip_pit_readiness(db)
    assert report["strict_usable_dates"] == 1
    assert report["usable"][0]["calendar_exact"] is False
    assert report["usable"][0]["entry_cutoff"] == "2026-09-25T09:15:00+08:00"


def test_daily_run_captures_chips_after_sync_and_before_scan_without_blocking() -> None:
    script = (Path(__file__).resolve().parents[1] / "daily_run.ps1").read_text(encoding="utf-8-sig")
    capture = script.index("scripts\\capture_chip_pit.py' --db $DbPath --apply")
    assert script.index("sync_daily.py") < capture < script.index("Step 4 '全市场扫描'")
    block = script[capture:script.index("Step 3 ")]
    # 筹码是研究数据：失败只警告，不能把日用选股标记为失败
    assert "Die" not in block and "optionalFailed" not in block
    assert "[switch]$SkipChipCapture" in script


def test_cli_readiness_and_refusal_exit_codes(db: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import capture_chip_pit

    monkeypatch.setattr(sys, "argv", ["capture_chip_pit.py", "--db", str(db), "--readiness"])
    assert capture_chip_pit.main() == 0
    assert '"strict_usable_dates": 0' in capsys.readouterr().out

    missing = tmp_path / "missing.db"
    monkeypatch.setattr(sys, "argv", ["capture_chip_pit.py", "--db", str(missing)])
    assert capture_chip_pit.main() == 2
    assert "REFUSED" in capsys.readouterr().out


def test_malformed_trade_date_is_refused(db: Path) -> None:
    with pytest.raises(ChipCaptureError, match="YYYYMMDD"):
        capture_chip_snapshot(db, None, trade_date="2026-09-24")


def test_revision_lookup_uses_primary_key_not_full_scan(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        plan = " ".join(
            str(row[-1])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT ts_code, revision, content_hash FROM cyq_history"
                " WHERE ts_code IN (?,?) AND trade_date=? ORDER BY ts_code, revision",
                ("000001.SZ", "600000.SH", DAY),
            ).fetchall()
        )
    assert "SCAN cyq_history" not in plan
    assert "USING" in plan
