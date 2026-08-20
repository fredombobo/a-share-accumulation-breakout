"""Astock 情报桥：涨停/跌停梯队测试（板宽口径 + 空日 INSUFFICIENT）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.intelligence.limit_up import board_limit_pct, limit_up_ladder


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "limit_up.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
        )
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, close, pre_close) VALUES (?,?,?,?)",
            [
                # 主板 10%：涨停（pct = (11.0/10 - 1)*100 = 10.0 >= 9.9）
                ("000001.SZ", "20260810", 11.0, 10.0),
                # 创业板 20%：pct=10 不是涨停（< 19.9）
                ("300001.SZ", "20260810", 11.0, 10.0),
                # 创业板 20%：pct=20 涨停（>= 19.9）
                ("300002.SZ", "20260810", 12.0, 10.0),
                # 科创 20%：跌停（pct = (8.0/10 - 1)*100 = -20 <= -19.9）
                ("688001.SH", "20260810", 8.0, 10.0),
                # 普通下跌，非跌停
                ("000002.SZ", "20260810", 9.8, 10.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_board_limit_pct():
    assert board_limit_pct("000001.SZ") == 10.0
    assert board_limit_pct("600000.SH") == 10.0
    assert board_limit_pct("300001.SZ") == 20.0
    assert board_limit_pct("301001.SZ") == 20.0
    assert board_limit_pct("688001.SH") == 20.0


def test_limit_up_ladder_counts(db: str):
    r = limit_up_ladder(db, "20260810")
    assert r["status"] == "PASS"
    # 涨停：000001.SZ（10%）、300002.SZ（20%）→ 2
    assert r["limit_up"] == 2
    # 跌停：688001.SH → 1
    assert r["limit_down"] == 1
    codes = [i["ts_code"] for i in r["items"]]
    assert "000001.SZ" in codes
    assert "300002.SZ" in codes
    # 300001.SZ（pct=10）不得计入涨停
    assert "300001.SZ" not in codes


def test_limit_up_ladder_sorted_desc(db: str):
    r = limit_up_ladder(db, "20260810")
    pcts = [i["pct_chg"] for i in r["items"]]
    assert pcts == sorted(pcts, reverse=True)
    # board_limit_pct 字段存在且正确
    for item in r["items"]:
        assert item["board_limit_pct"] in (10.0, 20.0)


def test_limit_up_ladder_empty_day(db: str):
    r = limit_up_ladder(db, "19990101")
    assert r["status"] == "INSUFFICIENT"
    assert r["limit_up"] == 0
    assert r["items"] == []


def test_limit_up_ladder_caps_at_20(tmp_path: Path):
    path = tmp_path / "many.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
        " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
    )
    rows = [
        (f"{i:06d}.SZ", "20260810", 11.0, 10.0) for i in range(1, 26)
    ]
    conn.executemany(
        "INSERT INTO daily (ts_code, trade_date, close, pre_close) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    r = limit_up_ladder(str(path), "20260810", top_n=50)
    assert r["limit_up"] == 25
    assert len(r["items"]) == 20
