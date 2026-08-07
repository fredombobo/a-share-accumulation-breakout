"""阶段1 验收：BEGIN IMMEDIATE + 幂等键 → 并发确认同一订单只产生一次资产预留。

模拟：N 线程同时确认同一 order_id（同 idempotency_key），
只有 1 个线程能成功写入预留，其余因 UNIQUE 约束失败。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading.db import tx  # noqa: E402
from paper_trading.migrations import run_migrations  # noqa: E402

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _db() -> str:
    td = tempfile.TemporaryDirectory()
    _TMP_DIRS.append(td)
    db = os.path.join(td.name, "stock_data.db")
    run_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_account VALUES (1,1000000,'ACTIVE',1,"
        " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')"
    )
    conn.commit()
    conn.close()
    return db


def _try_reserve(db: str, order_id: str, key: str, results: list, idx: int) -> None:
    """单线程预留尝试：BEGIN IMMEDIATE 内 INSERT OR IGNORE pt_order。"""
    try:
        with tx(db, immediate=True) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO pt_order (order_id, idempotency_key, account_id,"
                " source, ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
                " VALUES (?,?,1,'SCAN','000001.SZ','BUY',100,'CONFIRMED',10000,"
                " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')",
                (order_id, key),
            )
            conn.commit() if cur.rowcount > 0 else conn.rollback()
        results[idx] = cur.rowcount
    except Exception as e:  # noqa: BLE001
        results[idx] = f"ERR:{type(e).__name__}"


def test_concurrent_reserve_only_once():
    """N 线程同时确认同一订单：仅 1 次成功预留。"""
    db = _db()
    N = 8
    results: list = [None] * N
    threads = [
        threading.Thread(target=_try_reserve, args=(db, "o_concurrent", "same_key", results, i))
        for i in range(N)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    successes = [r for r in results if r == 1]
    assert len(successes) == 1, f"应恰 1 次预留成功, 实际 {successes} (results={results})"
    conn = sqlite3.connect(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM pt_order WHERE order_id='o_concurrent'"
    ).fetchone()[0]
    conn.close()
    assert n == 1, f"订单应恰 1 条, 实际 {n}"
    print(f"[PASS] 并发 {N} 线程确认同订单：仅 {len(successes)} 次预留成功, 库内恰 1 条")


def test_sequential_same_key_second_fails():
    """顺序重放同一幂等键：第二次不产生新预留。"""
    db = _db()
    with tx(db, immediate=True) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO pt_order (order_id, idempotency_key, account_id,"
            " source, ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
            " VALUES ('o_seq','key_seq',1,'SCAN','000001.SZ','BUY',100,'CONFIRMED',10000,"
            " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')",
        )
        assert cur.rowcount == 1
    # 重放：同 key 同内容 → INSERT OR IGNORE 返回 0 行
    with tx(db, immediate=True) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO pt_order (order_id, idempotency_key, account_id,"
            " source, ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
            " VALUES ('o_seq2','key_seq',1,'SCAN','000001.SZ','BUY',100,'CONFIRMED',10000,"
            " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')",
        )
        assert cur.rowcount == 0, "重放同 key 应被 IGNORE"
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM pt_order WHERE idempotency_key='key_seq'").fetchone()[0]
    conn.close()
    assert n == 1
    print("[PASS] 顺序重放同幂等键：第二次 0 影响")
