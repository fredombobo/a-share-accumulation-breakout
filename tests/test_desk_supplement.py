"""Astock 情报桥：desk-supplement 组装测试（G1 只读 + G8 契约字段 + 不写库）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.intelligence.desk_supplement import (
    build_desk_supplement,
    latest_trade_date,
)


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "supplement.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
        )
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, close, pre_close) VALUES (?,?,?,?)",
            [
                ("000001.SZ", "20260810", 11.0, 10.0),   # 主板涨停
                ("000002.SZ", "20260810", 9.8, 10.0),    # 下跌
                ("000003.SZ", "20260810", 10.5, 10.0),   # 上涨
                ("000001.SH", "20260810", 3100.0, 3090.0),  # 指数
                ("000300.SH", "20260810", 4000.0, 3950.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _table_count(db: str, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_latest_trade_date(db: str):
    assert latest_trade_date(db) == "20260810"
    assert latest_trade_date(Path(db).parent / "nope.db") is None


def test_desk_supplement_gate_fields(db: str, monkeypatch):
    monkeypatch.delenv("ASTOCK_BASE_URL", raising=False)
    r = build_desk_supplement(db, "20260810", include_http=True)
    # G8 契约字段
    assert r["side_effects"] is False
    assert r["not_a_pool"] is True
    assert "disclaimer" in r
    assert "不进入 A 池" in r["disclaimer"]
    # 结构
    assert r["trade_date"] == "20260810"
    assert r["status"] == "PASS"
    assert "breadth" in r
    assert "limit_up" in r
    assert "indices" in r
    assert "astock" in r


def test_desk_supplement_astock_disabled(db: str, monkeypatch):
    monkeypatch.delenv("ASTOCK_BASE_URL", raising=False)
    r = build_desk_supplement(db, "20260810")
    assert r["astock"]["enabled"] is False
    assert r["astock"]["reachable"] is False


def test_desk_supplement_readonly_g1(db: str, monkeypatch):
    """G1：调用前后 daily 行数不变；scan_/pt_ 表不存在则跳过。"""
    monkeypatch.delenv("ASTOCK_BASE_URL", raising=False)
    before = _table_count(db, "daily")
    conn = sqlite3.connect(db)
    names = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    extra = [t for t in names if t.startswith("scan_") or t.startswith("pt_")]
    extra_before = {t: _table_count(db, t) for t in extra}
    build_desk_supplement(db, "20260810")
    after = _table_count(db, "daily")
    assert before == after == 5
    for t, n in extra_before.items():
        assert _table_count(db, t) == n


def test_desk_supplement_empty_db(tmp_path: Path, monkeypatch):
    """空库 → status=INSUFFICIENT + reason=no_trade_date。"""
    monkeypatch.delenv("ASTOCK_BASE_URL", raising=False)
    empty = tmp_path / "empty.db"
    conn = sqlite3.connect(str(empty))
    conn.execute(
        "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
        " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
    )
    conn.commit()
    conn.close()
    r = build_desk_supplement(empty, None)
    assert r["status"] == "INSUFFICIENT"
    assert r["reason"] == "no_trade_date"
    assert r["side_effects"] is False
    assert r["not_a_pool"] is True
