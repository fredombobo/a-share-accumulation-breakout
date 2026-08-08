"""阶段1 验收：迁移不修改/删除原行情及扫描记录。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading.migrations import run_migrations


def _build_legacy_db(db: str, n_days: int = 5, n_codes: int = 3) -> None:
    """构造老库：daily（无元数据列）+ scan_result + stock_basic，含数据。"""
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
    conn.execute("CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
    rows = []
    codes = ["000001.SZ", "600001.SH", "300001.SZ"]
    for i in range(n_days):
        td = f"202607{2 + i:02d}"
        for c in codes:
            rows.append((c, td, 10.0 + i, 10.5 + i, 9.9 + i, 10.2 + i, 10.0 + i,
                         0.2, 1.96, 1000.0, 10000.0))
    conn.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO scan_result VALUES (?,?,?,?)",
        [(f"202607{2 + i:02d}", codes[i % 3], f"股票{i}", 80.0 + i) for i in range(n_days)],
    )
    conn.executemany("INSERT INTO stock_basic VALUES (?,?)", [(c, f"名称{c}") for c in codes])
    conn.commit()
    conn.close()


def test_migration_preserves_daily_rows_and_values():
    """迁移前后 daily 行数与采样行旧列值逐列一致，无 DROP。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    _build_legacy_db(db)

    before = _snapshot(db, "daily")
    assert len(before) == 15  # 3 codes × 5 days

    run_migrations(db)
    after = _snapshot(db, "daily")

    assert len(after) == len(before) == 15, "行数变化（发生 DROP/重写）"
    before_by_key = {(r["ts_code"], r["trade_date"]): r for r in before}
    for r_after in after:
        key = (r_after["ts_code"], r_after["trade_date"])
        r_before = before_by_key[key]
        # 旧列值（前 11 列）必须逐列一致
        for col in ("ts_code", "trade_date", "open", "high", "low", "close",
                    "pre_close", "change", "pct_chg", "vol", "amount"):
            assert r_after[col] == r_before[col], f"{key} 列 {col} 被改动"
        # 新列必须存在且已标记 legacy
        assert r_after["source"] == "legacy_backfill"
        assert r_after["is_legacy"] == 1
        assert r_after["available_at"] and "09:30:00+08:00" in r_after["available_at"]
    print(f"[PASS] daily {len(after)} 行旧值逐列一致, 元数据列已填充")


def test_migration_preserves_scan_result_and_stock_basic():
    """迁移前后 scan_result / stock_basic 完全不变。"""
    td = tempfile.TemporaryDirectory()
    db = os.path.join(td.name, "stock_data.db")
    _build_legacy_db(db)

    sr_before = _snapshot(db, "scan_result")
    sb_before = _snapshot(db, "stock_basic")

    run_migrations(db)

    sr_after = _snapshot(db, "scan_result")
    sb_after = _snapshot(db, "stock_basic")
    assert sr_after == sr_before, "scan_result 被修改"
    assert sb_after == sb_before, "stock_basic 被修改"
    print(f"[PASS] scan_result {len(sr_after)} 行 / stock_basic {len(sb_after)} 行不变")

def _snapshot(db: str, table: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    conn.close()
    return [dict(r) for r in rows]
