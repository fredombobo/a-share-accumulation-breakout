"""阶段1 验收：迁移机制（空库 / 副本升级 / 重复执行幂等）"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_store import LocalStore  # noqa: E402
from paper_trading.migrations import (  # noqa: E402
    MIGRATIONS,
    current_schema_version,
    list_paper_tables,
    run_migrations,
)
from paper_trading.schema import PAPER_TABLE_NAMES  # noqa: E402


def _tmp_db() -> str:
    td = tempfile.TemporaryDirectory()
    return os.path.join(td.name, "stock_data.db")


def test_empty_db_migrates_all():
    """验收①：空库迁移 → schema_version 全版本 + 14 张领域表齐。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    applied = run_migrations(db)
    assert applied == [v for v, _, _ in MIGRATIONS]
    assert current_schema_version(db) == max(v for v, _, _ in MIGRATIONS)
    tables = list_paper_tables(db)
    assert len(tables) == len(PAPER_TABLE_NAMES) == 14
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
    assert len(list_paper_tables(db)) == 14
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
    s = LocalStore(db_path=db)
    assert current_schema_version(db) >= 4
    assert len(list_paper_tables(db)) == 14
    LocalStore(db_path=db)  # 再次创建，不抛错
    assert current_schema_version(db) >= 4
    print("[PASS] LocalStore 自动迁移 + 重复创建幂等")
