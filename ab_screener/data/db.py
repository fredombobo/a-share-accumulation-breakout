"""SQLite 连接助手（data 层；API 不得直接 import sqlite3）。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SchemaMissing(RuntimeError):
    """表未迁移。"""


@contextmanager
def connect(db_path: str | Path, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    if readonly:
        conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        conn.close()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)
