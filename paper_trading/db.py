"""显式事务上下文：BEGIN IMMEDIATE（防并发写锁竞争/双花）。

- tx(db_path, immediate=True)：显式控制事务，yield conn，正常 commit / 异常 rollback
- 多步账本写（预留→下单→记流水→批次）必须包在同一个 BEGIN IMMEDIATE 事务内
- 配合 pt_order.idempotency_key UNIQUE + INSERT OR IGNORE，并发确认只成功一次
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def tx(db_path: str | Path, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """开启一个显式事务（默认 BEGIN IMMEDIATE 拿写锁）。"""
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)  # 显式事务控制
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()
