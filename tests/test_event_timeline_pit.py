"""P1.4 事件时间线 PIT 测试：available_at、过滤、状态投影、快照指纹失效。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.application.corporate_action_service import (
    ingest_dividend,
    ingest_split,
    reversal,
)
from ab_screener.data.intelligence_repository import (
    dataset_status,
    latest_manifest,
    snapshot_fingerprint,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.pit_writer import write_plain
from ab_screener.intelligence.quality import data_source_status
from ab_screener.intelligence.timeline import (
    corporate_action_timeline,
    timeline_summary,
)


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "tl.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return str(path)


def test_corporate_action_timeline_pit(db: str):
    ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    ingest_split(db, ts_code="000001.SZ", ex_date="20260720", ratio=2.0)
    events = corporate_action_timeline(db, "000001.SZ")
    assert [e.kind for e in events] == ["DIVIDEND", "SPLIT"]
    assert all(e.available_at for e in events)          # PIT 时间必须存在
    assert all(e.status == "PENDING" for e in events)   # 状态投影
    # 过滤
    only_split = corporate_action_timeline(db, "000001.SZ", kinds={"SPLIT"})
    assert [e.kind for e in only_split] == ["SPLIT"]
    ranged = corporate_action_timeline(db, "000001.SZ", start="20260715", end="20260731")
    assert [e.kind for e in ranged] == ["SPLIT"]
    summary = timeline_summary(events)
    assert summary["count"] == 2 and summary["kinds"] == ["DIVIDEND", "SPLIT"]


def test_timeline_reflects_reversal_status(db: str):
    aid = ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    reversal(db, original_id=aid, payload={"cash_div_fen": 180, "reason": "更正"})
    events = corporate_action_timeline(db, "000001.SZ")
    kinds = [(e.kind, e.status) for e in events]
    assert ("DIVIDEND", "REVERSED") in kinds
    assert ("REVERSAL", "PENDING") in kinds


def test_snapshot_fingerprint_invalidates_on_new_manifest(db: str):
    conn = sqlite3.connect(db)
    try:
        write_plain(
            conn, "daily",
            [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}],
            source="tushare", available_at="2026-08-10T16:00:00+08:00", partition_key="20260810",
        )
    finally:
        conn.close()
    fp1 = snapshot_fingerprint(db, "daily", "20260810")
    manifest1 = latest_manifest(db, "daily", "20260810")
    assert manifest1 is not None and manifest1.row_count == 1
    # 修订（新 manifest）→ 指纹变化（缓存按 manifest 失效）
    conn = sqlite3.connect(db)
    try:
        write_plain(
            conn, "daily",
            [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.5}],
            source="tushare", available_at="2026-08-11T09:00:00+08:00", partition_key="20260810",
        )
    finally:
        conn.close()
    fp2 = snapshot_fingerprint(db, "daily", "20260810")
    assert fp1 != fp2


def test_dataset_status_and_source_status(db: str):
    conn = sqlite3.connect(db)
    try:
        write_plain(
            conn, "daily",
            [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}],
            source="tushare", available_at="2026-08-10T16:00:00+08:00", partition_key="20260810",
        )
        # 主表 daily（信息模块读取新鲜度的对象）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,"
            " PRIMARY KEY (ts_code, trade_date))"
        )
        conn.execute(
            "INSERT INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
            " VALUES ('000001.SZ','20260810',10.0,10.5,9.8,10.2,1000,1e7)"
        )
        conn.commit()
    finally:
        conn.close()
    status = dataset_status(db)
    assert "daily" in status and status["daily"]["rows"] == 1
    src = data_source_status(db)
    assert src["daily_latest_trade_date"] == "20260810"
