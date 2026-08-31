"""PIT 读取仓库：按 decision_at 读取历史版本（append-only 读取侧）。

契约（implementation P1.1）：
- `read_asof(dataset, business_key, decision_at)`：返回该业务键在 decision_at
  时刻应读取的版本（available_at <= decision_at 中 revision 最大者）。
- 同一业务键两次修订，修订前后 decision_at 分别返回旧/新版本。
- 读取侧不做任何写操作；写操作一律走 pit_writer。
- 时间统一 +08:00 比较；未知数据集 fail-closed。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ab_screener.data.pit_writer import HISTORY_TABLES
from ab_screener.domain.data_point import normalize_ts


def _table_for(dataset: str) -> str:
    table = f"{dataset}_history"
    if table not in HISTORY_TABLES:
        raise ValueError(f"未知 PIT 数据集: {dataset}（历史表: {sorted(HISTORY_TABLES)}）")
    return table


def _parse(row: tuple[Any, ...], key_cols: list[str]) -> dict[str, Any] | None:
    if row is None:
        return None
    n = len(key_cols)
    return {
        "business_key": {c: row[i] for i, c in enumerate(key_cols)},
        "revision": int(row[n]),
        "available_at": row[n + 1],
        "source": row[n + 2],
        "content_hash": row[n + 3],
        "payload": json_loads(row[n + 4]),
    }


def json_loads(text: str) -> dict[str, Any]:
    import json

    return json.loads(text)


class PitRepository:
    """基于单个 SQLite 连接/文件的 PIT 读取仓库。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)

    def read_asof(
        self,
        dataset: str,
        business_key: dict[str, str],
        decision_at: Any,
    ) -> dict[str, Any] | None:
        """decision_at 时刻该业务键的版本；无可用版本返回 None。"""
        table = _table_for(dataset)
        key_cols = HISTORY_TABLES[table]
        decision = normalize_ts(decision_at)
        where = " AND ".join(f"{c}=?" for c in key_cols)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {','.join(key_cols)}, revision, available_at, source,"
                f" content_hash, payload_json FROM {table}"
                f" WHERE {where} AND available_at <= ?"
                " ORDER BY revision DESC LIMIT 1",
                (*[str(business_key[c]) for c in key_cols], decision),
            ).fetchone()
        return _parse(row, key_cols)

    def read_all(
        self,
        dataset: str,
        business_key: dict[str, str],
    ) -> list[dict[str, Any]]:
        """该业务键的全部修订（升序 revision），供 as-of 语义测试。"""
        table = _table_for(dataset)
        key_cols = HISTORY_TABLES[table]
        where = " AND ".join(f"{c}=?" for c in key_cols)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {','.join(key_cols)}, revision, available_at, source,"
                f" content_hash, payload_json FROM {table}"
                f" WHERE {where} ORDER BY revision ASC",
                [str(business_key[c]) for c in key_cols],
            ).fetchall()
        return [r for r in (_parse(row, key_cols) for row in rows) if r is not None]

    def list_datasets(self) -> list[str]:
        with self._connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%\\_history' ESCAPE '\\'"
                ).fetchall()
            }
        return sorted(t for t in tables if t in HISTORY_TABLES)

    def manifest_rows(self, dataset: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE dataset=?" if dataset else ""
        params: Iterable[Any] = (dataset,) if dataset else ()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT manifest_id, dataset, partition_key, source, available_at,"
                " row_count, content_sha256, ingested_at FROM raw_ingest_manifests"
                f" {where} ORDER BY ingested_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [
            {
                "manifest_id": r[0],
                "dataset": r[1],
                "partition_key": r[2],
                "source": r[3],
                "available_at": r[4],
                "row_count": r[5],
                "content_sha256": r[6],
                "ingested_at": r[7],
            }
            for r in rows
        ]
