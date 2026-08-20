"""Astock 情报桥：七指数快照测试（缺指数 INSUFFICIENT；有则 PASS）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.intelligence.indices import A_SHARE_INDICES, index_snapshot


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "indices.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
        )
        # 只给其中 2 个指数数据：上证、沪深300
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, close, pre_close) VALUES (?,?,?,?)",
            [
                ("000001.SH", "20260810", 3100.0, 3090.0),
                ("000300.SH", "20260810", 4000.0, 3950.0),
                # 非指数，干扰项，应被忽略
                ("000001.SZ", "20260810", 11.0, 10.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_a_share_indices_has_seven():
    assert len(A_SHARE_INDICES) == 7
    codes = [c for c, _ in A_SHARE_INDICES]
    assert codes[0] == "000001.SH"
    assert codes[3] == "000688.SH"


def test_index_snapshot_only_existing(db: str):
    r = index_snapshot(db, "20260810")
    assert r["status"] == "PASS"
    assert len(r["items"]) == 2  # 只返回 daily 中存在的指数
    codes = {i["ts_code"] for i in r["items"]}
    assert codes == {"000001.SH", "000300.SH"}
    # 沪深300 pct = (4000/3950 - 1)*100 ≈ 1.27
    hs300 = next(i for i in r["items"] if i["ts_code"] == "000300.SH")
    assert hs300["name"] == "沪深300"
    assert round(hs300["pct_chg"], 2) == 1.27
    assert r["coverage"] == round(2 / 7, 4)


def test_index_snapshot_missing_all(db: str):
    r = index_snapshot(db, "19990101")
    assert r["status"] == "INSUFFICIENT"
    assert r["items"] == []


def test_index_snapshot_missing_db(tmp_path: Path):
    r = index_snapshot(tmp_path / "nope.db", "20260810")
    assert r["status"] == "INSUFFICIENT"
    assert r["reason"] == "db_missing"
    assert r["items"] == []
