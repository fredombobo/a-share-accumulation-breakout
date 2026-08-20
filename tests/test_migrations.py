"""阶段1 验收：迁移机制（空库 / 副本升级 / 重复执行幂等）"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_store import LocalStore
from paper_trading.migrations import (
    MIGRATIONS,
    current_schema_version,
    list_paper_tables,
    run_migrations,
)
from paper_trading.schema import PAPER_TABLE_NAMES


def _tmp_db() -> str:
    td = tempfile.TemporaryDirectory()
    return os.path.join(td.name, "stock_data.db")


def test_empty_db_migrates_all():
    """验收①：空库迁移 → schema_version 全版本 + 领域表齐。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    applied = run_migrations(db)
    assert applied == [v for v, _, _ in MIGRATIONS]
    assert current_schema_version(db) == max(v for v, _, _ in MIGRATIONS)
    tables = list_paper_tables(db)
    assert len(tables) == len(PAPER_TABLE_NAMES) == 15
    assert tables == set(PAPER_TABLE_NAMES)
    print(f"[PASS] 空库迁移 {len(applied)} 个版本, {len(tables)} 张表")


def test_existing_db_upgrades_only_missing():
    """验收②：已有库（模拟 1GB 现网：预置 daily/scan_result 行）→ 只新增缺失版本。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    # 先建老库：仅 daily + scan_result（无元数据列、无领域表）
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
        " open REAL, high REAL, low REAL, close REAL, pre_close REAL, change REAL,"
        " pct_chg REAL, vol REAL, amount REAL, PRIMARY KEY (ts_code, trade_date))"
    )
    conn.execute(
        "CREATE TABLE scan_result (trade_date TEXT, ts_code TEXT, name TEXT,"
        " total_score REAL, PRIMARY KEY (trade_date, ts_code))"
    )
    conn.executemany(
        "INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("000001.SZ", "20260731", 10.0, 10.5, 9.9, 10.2, 10.0, 0.2, 1.96, 1000.0, 10000.0)],
    )
    conn.execute("INSERT INTO scan_result VALUES ('20260731','000001.SZ','测试',80.0)")
    conn.commit()
    conn.close()

    applied = run_migrations(db)
    assert applied == [v for v, _, _ in MIGRATIONS], f"老库应补齐全部缺失版本: {applied}"
    assert current_schema_version(db) == max(v for v, _, _ in MIGRATIONS)
    assert len(list_paper_tables(db)) == 15
    # 原数据保留
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT ts_code, trade_date, open, close FROM daily").fetchone()
    assert row == ("000001.SZ", "20260731", 10.0, 10.2), f"原行情被改动: {row}"
    sr = conn.execute("SELECT trade_date, ts_code, name, total_score FROM scan_result").fetchone()
    assert sr == ("20260731", "000001.SZ", "测试", 80.0), f"原扫描记录被改动: {sr}"
    conn.close()
    print("[PASS] 已有库补齐缺失版本, 原数据保留")


def test_repeat_migration_idempotent():
    """验收③：重复迁移 → no-op（版本全在，不重复应用）。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    run_migrations(db)
    # 记一次 schema_version 行数
    conn = sqlite3.connect(db)
    n_before = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    conn.close()
    applied = run_migrations(db)
    assert applied == [], f"重复迁移应 no-op: {applied}"
    conn = sqlite3.connect(db)
    n_after = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    conn.close()
    assert n_after == n_before, f"schema_version 行数变化: {n_before} -> {n_after}"
    print(f"[PASS] 重复迁移幂等 (schema_version={n_after} 行不变)")


def test_local_store_init_runs_migrations():
    """挂接验证：LocalStore.__init__ 自动迁移，重复创建幂等。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    LocalStore(db_path=db)
    assert current_schema_version(db) >= 4
    assert len(list_paper_tables(db)) == 15
    LocalStore(db_path=db)  # 再次创建，不抛错
    assert current_schema_version(db) >= 4
    print("[PASS] LocalStore 自动迁移 + 重复创建幂等")


def test_v7_migration_preserves_lots_and_allows_zero_balance():
    """旧批次表升级后数据不丢失，且完整核销可以归零。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " checksum TEXT NOT NULL, applied_at TEXT NOT NULL);"
        "CREATE TABLE pt_account (account_id INTEGER PRIMARY KEY);"
        "CREATE TABLE pt_fill (fill_id TEXT PRIMARY KEY);"
        "CREATE TABLE pt_position_lot (lot_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " account_id INTEGER NOT NULL, ts_code TEXT NOT NULL, buy_fill_id TEXT NOT NULL,"
        " remaining_qty INTEGER NOT NULL CHECK (remaining_qty > 0),"
        " cost_price_micro INTEGER NOT NULL CHECK (cost_price_micro > 0),"
        " sellable_date TEXT NOT NULL, created_at TEXT NOT NULL);"
        "INSERT INTO pt_position_lot VALUES (1,1,'000001.SZ','opening-1',200,10000000,"
        " '20260801','2026-08-01T10:00:00+08:00');"
        "INSERT INTO pt_account VALUES (1);"
        "INSERT INTO pt_fill VALUES ('opening-1');"
    )
    conn.executemany(
        "INSERT INTO schema_version VALUES (?,?,'old','2026-08-01T00:00:00+08:00')",
        [(version, f"old-{version}") for version in range(1, 7)],
    )
    conn.commit()
    conn.close()

    assert run_migrations(db) == [7, 8, 9]
    conn = sqlite3.connect(db)
    before = conn.execute(
        "SELECT lot_id, remaining_qty, cost_price_micro FROM pt_position_lot"
    ).fetchone()
    conn.execute("UPDATE pt_position_lot SET remaining_qty=0 WHERE lot_id=1")
    conn.commit()
    after = conn.execute(
        "SELECT lot_id, remaining_qty, cost_price_micro FROM pt_position_lot"
    ).fetchone()
    conn.close()
    assert before == (1, 200, 10_000_000)
    assert after == (1, 0, 10_000_000)


def test_upsert_daily_writes_complete_point_in_time_metadata():
    """任何在线日线写入都必须由存储边界补齐完整 PIT 元数据。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    store = LocalStore(db_path=db)

    store.upsert_daily(pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20260807",
        "open": 11.23, "high": 11.26, "low": 11.10, "close": 11.19,
        "pre_close": 11.19, "change": 0.0, "pct_chg": 0.0,
        "vol": 100.0, "amount": 1000.0,
    }]))

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT effective_at, available_at, ingested_at, source, revision, is_legacy "
        "FROM daily WHERE ts_code='000001.SZ' AND trade_date='20260807'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "2026-08-07T15:00:00+08:00"
    assert row[1] and row[1].endswith("+08:00")
    assert row[2] and row[2].endswith("+08:00")
    assert row[3:] == ("tushare", 1, 0)


def test_v8_migration_backfills_effective_at_for_recent_rows():
    """v6 以后新增但缺 effective_at 的行必须由前向迁移修复。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " checksum TEXT NOT NULL, applied_at TEXT NOT NULL);"
        "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
        " open REAL, high REAL, low REAL, close REAL, pre_close REAL, change REAL,"
        " pct_chg REAL, vol REAL, amount REAL, available_at TEXT, ingested_at TEXT,"
        " source TEXT, revision INTEGER, is_legacy INTEGER, effective_at TEXT,"
        " PRIMARY KEY(ts_code,trade_date));"
        "INSERT INTO daily VALUES ('000001.SZ','20260807',11.23,11.26,11.10,11.19,"
        " 11.19,0,0,100,1000,'2026-08-08T09:00:00+08:00',"
        " '2026-08-08T09:00:00+08:00','tushare',1,0,NULL);"
    )
    conn.executemany(
        "INSERT INTO schema_version VALUES (?,?,'old','2026-08-01T00:00:00+08:00')",
        [(version, f"old-{version}") for version in range(1, 8)],
    )
    conn.commit()
    conn.close()

    assert run_migrations(db) == [8, 9]
    conn = sqlite3.connect(db)
    effective_at = conn.execute("SELECT effective_at FROM daily").fetchone()[0]
    conn.close()
    assert effective_at == "2026-08-07T15:00:00+08:00"
