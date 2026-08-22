"""P1.3 数据质量门禁测试：重复键/OHLC/覆盖率/源端比对/无 Token INSUFFICIENT。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import ab_screener.local_store as local_store_mod
from ab_screener.application.data_quality import (
    check_coverage,
    check_duplicate_keys,
    check_invalid_ohlc_and_negative,
    run_data_quality,
    source_parity,
)
from ab_screener.data.instrument_repository import upsert_instrument
from ab_screener.data.migration_registry import apply_pending
from ab_screener.domain.instrument import Instrument


@pytest.fixture()
def db(tmp_path: Path, monkeypatch) -> str:
    path = tmp_path / "dq.db"
    monkeypatch.setattr(local_store_mod, "_DB_PATH", path)
    from local_store import LocalStore

    LocalStore()
    # 基础表 + v2 表
    conn = sqlite3.connect(str(path))
    try:
        apply_pending(conn)
        # 三只股票规则：两只上市、一只已退市
        upsert_instrument(conn, Instrument(ts_code="000001.SZ", name="平安银行", exchange="SZSE",
                                           security_type="stock", list_date="19910403"))
        upsert_instrument(conn, Instrument(ts_code="600000.SH", name="浦发银行", exchange="SSE",
                                           security_type="stock", list_date="19991110"))
        upsert_instrument(conn, Instrument(ts_code="600999.SH", name="示例退市", exchange="SSE",
                                           security_type="stock", list_date="20000101",
                                           delist_date="20230101"))
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _seed_daily(db: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(db)
    try:
        for ts_code, trade_date, open_, high, low, close, vol, amount in rows:
            conn.execute(
                "INSERT INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (ts_code, trade_date, open_, high, low, close, vol, amount),
            )
        conn.commit()
    finally:
        conn.close()


def test_duplicate_keys_and_ohlc(db: str):
    _seed_daily(db, [
        ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
        ("600000.SH", "20260810", 5.0, 5.2, 4.9, 5.1, 2000, 1e7),
    ])
    assert check_duplicate_keys(db)["pass"] is True
    assert check_invalid_ohlc_and_negative(db)["pass"] is True
    # 非法 OHLC：high < close
    _seed_daily(db, [("600000.SH", "20260811", 5.0, 4.8, 4.7, 5.2, 100, 1e6)])
    assert check_invalid_ohlc_and_negative(db)["pass"] is False
    # 负量额
    _seed_daily(db, [("600000.SH", "20260812", 5.0, 5.2, 4.9, 5.1, -1, 1e6)])
    assert check_invalid_ohlc_and_negative(db)["pass"] is False


def test_duplicate_keys_detected(tmp_path: Path):
    """防御性检查：无约束表里混入重复键时能检出（真实表有 PK，不可能出现重复）。"""
    path = tmp_path / "dups.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE daily (ts_code TEXT, trade_date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, vol REAL, amount REAL)"
    )
    conn.executemany(
        "INSERT INTO daily VALUES (?,?,?,?,?,?,?,?)",
        [
            ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
            ("000001.SZ", "20260810", 10.1, 10.6, 9.9, 10.3, 1100, 1e7),  # 重复键
        ],
    )
    conn.commit()
    conn.close()
    check = check_duplicate_keys(str(path))
    assert check["pass"] is False and check["count"] == 1


def test_coverage_with_active_universe(db: str):
    _seed_daily(db, [
        ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
        ("600000.SH", "20260810", 5.0, 5.2, 4.9, 5.1, 2000, 1e7),
    ])
    # 活跃规则 = 000001.SZ + 600000.SH（600999.SH 已退市 2023）
    check = check_coverage(db, "20260810")
    assert check["pass"] is True
    assert check["total"] == 2 and check["covered"] == 2


class FakePro:
    """源端比对 fake：与本地一致的行情。"""

    def daily(self, **kwargs):
        d = kwargs["end_date"]
        return pd.DataFrame(
            {"ts_code": [kwargs["ts_code"]], "trade_date": [d],
             "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
             "vol": [1000], "amount": [1e7]}
        )


class FakeProMismatch(FakePro):
    def daily(self, **kwargs):
        df = super().daily(**kwargs)
        df.loc[0, "close"] = 10.9  # 与本地 10.2 不一致
        return df


def test_source_parity_zero_diff(db: str):
    _seed_daily(db, [("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7)])
    check = source_parity(db, pro=FakePro(), codes=["000001.SZ"],
                          latest_trade_date="20260810")
    assert check["pass"] is True and check["result"] == "PASS" and check["diffs"] == 0


def test_source_parity_detects_diff(db: str):
    _seed_daily(db, [("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7)])
    check = source_parity(db, pro=FakeProMismatch(), codes=["000001.SZ"],
                          latest_trade_date="20260810")
    assert check["pass"] is False and check["diffs"] >= 1


def test_no_token_is_insufficient_not_pass(db: str):
    # 两只活跃标的全覆盖，其余检查全过；仅源端比对因无 Token 无法执行 → INSUFFICIENT
    _seed_daily(db, [
        ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
        ("600000.SH", "20260810", 5.0, 5.2, 4.9, 5.1, 2000, 1e7),
    ])
    report = run_data_quality(db, as_of="20260810", pro=None)
    assert report["result"] == "INSUFFICIENT"
    assert any(c["name"] == "source_parity" and c["result"] == "INSUFFICIENT"
               for c in report["checks"])


def test_full_quality_pass_with_fake_source(db: str):
    _seed_daily(db, [
        ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
        ("600000.SH", "20260810", 5.0, 5.2, 4.9, 5.1, 2000, 1e7),
    ])
    report = run_data_quality(
        db, as_of="20260810", pro=FakePro(), latest_trade_date="20260810",
        seed_codes=["000001.SZ"],
    )
    assert report["result"] == "PASS"
