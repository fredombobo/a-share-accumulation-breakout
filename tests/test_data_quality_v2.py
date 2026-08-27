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
    shadow_parity,
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


def _seed_full_parity(db: str, pit_skip_code: str | None = None) -> tuple[list[str], list[str]]:
    """足量 parity fixture：20 标的 × 5 日期（=100 样本 × 6 字段 = 600 比较）。

    legacy daily 与 PIT daily_history 同值写入；pit_skip_code 指定的标的跳过 PIT 写入。
    """
    from ab_screener.data.pit_writer import write_plain

    dates = ["20260803", "20260804", "20260805", "20260806", "20260807"]
    codes = [f"{i:06d}.SZ" for i in range(1, 21)]  # 000001.SZ .. 000020.SZ
    daily_rows: list[tuple] = []
    pit_by_date: dict[str, list[dict]] = {d: [] for d in dates}
    for idx, code in enumerate(codes):
        for di, d in enumerate(dates):
            px = 10.0 + idx * 0.1 + di * 0.01
            row = {
                "ts_code": code, "trade_date": d,
                "open": px, "high": px + 0.2, "low": px - 0.2, "close": px + 0.1,
                "vol": 1000.0 + idx, "amount": 1e7,
            }
            daily_rows.append(
                (code, d, row["open"], row["high"], row["low"], row["close"], row["vol"], row["amount"])
            )
            if code != pit_skip_code:
                pit_by_date[d].append(row)
    _seed_daily(db, daily_rows)
    conn = sqlite3.connect(db)
    try:
        for d in dates:
            if pit_by_date[d]:
                write_plain(
                    conn, "daily", pit_by_date[d], source="tushare",
                    available_at=f"2026-08-{int(d[6:8]):02d}T16:00:00+08:00",
                    partition_key=d,
                )
        conn.commit()
    finally:
        conn.close()
    return codes, dates


def test_shadow_parity_legacy_matches_pit(db: str):
    """legacy daily 与 PIT as-of 读取零差异 → PASS（足量 20×5=600 字段比较）。"""
    codes, dates = _seed_full_parity(db)
    report = shadow_parity(
        db, seed=7, codes=codes, dates=dates,
        decision_at="2026-08-11T00:00:00+08:00",
    )
    assert report["result"] == "PASS"
    assert report["diffs"] == []
    assert report["samples_checked"] == 100
    assert report["pairs_compared"] == 600
    assert report["code_sha"]
    assert report["config_hash"]
    assert report["db_fingerprint"]


def test_shadow_parity_detects_pit_missing(db: str):
    """足量样本中一只标的 PIT 缺失 → 差异且 result=FAIL。"""
    codes, dates = _seed_full_parity(db, pit_skip_code="000005.SZ")
    report = shadow_parity(
        db, seed=7, codes=codes, dates=dates,
        decision_at="2026-08-11T00:00:00+08:00",
    )
    assert report["result"] == "FAIL"
    assert any("缺失" in str(d["detail"]) for d in report["diffs"])


def test_shadow_parity_explicit_small_sample_insufficient(db: str):
    """RW-001：显式传入小样本（2 标的 × 1 日期）同样 INSUFFICIENT，不得 PASS。"""
    _seed_daily(db, [
        ("000001.SZ", "20260810", 10.0, 10.5, 9.8, 10.2, 1000, 1e7),
        ("600000.SH", "20260810", 5.0, 5.2, 4.9, 5.1, 2000, 1e7),
    ])
    report = shadow_parity(
        db, seed=7, codes=["000001.SZ", "600000.SH"],
        dates=["20260810"], decision_at="2026-08-11T00:00:00+08:00",
    )
    assert report["result"] == "INSUFFICIENT"
    assert report["pass"] is False
    assert "样本不足" in report["reason"]


def test_shadow_parity_insufficient_when_default_sample_too_small(tmp_path: Path, monkeypatch) -> None:
    """默认采样（门禁报告路径）样本不足 20 标的 × 5 日期 → INSUFFICIENT，不得误判 PASS。"""
    path = tmp_path / "empty.db"
    monkeypatch.setattr(local_store_mod, "_DB_PATH", path)
    from local_store import LocalStore

    LocalStore()
    conn = sqlite3.connect(str(path))
    try:
        apply_pending(conn)
        # 只有 1 只标的 × 1 日期 → 默认采样覆盖不足
        conn.execute(
            "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount)"
            " VALUES ('000001.SZ','20260810',10,10,10,10,100,1000)"
        )
        conn.execute(
            "INSERT INTO daily_history(ts_code,trade_date,revision,available_at,source,content_hash,payload_json)"
            " VALUES ('000001.SZ','20260810',1,'2026-08-10T16:00:00+08:00','tushare','h',"
            "'{\"open\":10,\"high\":10,\"low\":10,\"close\":10,\"vol\":100,\"amount\":1000}')"
        )
        conn.commit()
    finally:
        conn.close()

    report = shadow_parity(path, seed=7, decision_at="2026-08-11T00:00:00+08:00")
    assert report["result"] == "INSUFFICIENT"
    assert "样本不足" in report["reason"]


def test_shadow_parity_default_sampling_finds_dense_window_among_sparse_dates(db: str):
    """默认采样应找到真实 20×5 交集，不被互不相交的稀疏 PIT 日期干扰。"""
    from ab_screener.data.pit_writer import write_plain

    _seed_full_parity(db)
    conn = sqlite3.connect(db)
    try:
        for day_index, trade_date in enumerate(
            ["20260810", "20260811", "20260812", "20260813", "20260814"]
        ):
            rows: list[dict] = []
            for code_index in range(20):
                code = f"{300000 + day_index * 100 + code_index:06d}.SZ"
                price = 20.0 + day_index + code_index / 100
                conn.execute(
                    "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (code, trade_date, price, price + 0.2, price - 0.2,
                     price + 0.1, 1000.0, 1e7),
                )
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "open": price,
                        "high": price + 0.2,
                        "low": price - 0.2,
                        "close": price + 0.1,
                        "vol": 1000.0,
                        "amount": 1e7,
                    }
                )
            write_plain(
                conn,
                "daily",
                rows,
                source="tushare",
                available_at=f"2026-08-{10 + day_index:02d}T16:00:00+08:00",
                partition_key=trade_date,
            )
        conn.commit()
    finally:
        conn.close()

    report = shadow_parity(
        db,
        seed=7,
        decision_at="2026-08-20T00:00:00+08:00",
    )
    assert report["result"] == "PASS"
    assert report["samples_checked"] == 100
    assert report["pairs_compared"] == 600
