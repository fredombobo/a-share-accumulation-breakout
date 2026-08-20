"""SQLite repository：扫描/行情只读查询（禁止 pickle）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "runtime" / "stock_data.db"


class MarketRepository:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or _DEFAULT_DB)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def max_trade_date(self, table: str = "daily") -> str | None:
        allowed = {"daily", "daily_basic", "moneyflow"}
        if table not in allowed:
            raise ValueError(table)
        with self.connect() as conn:
            row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
            return row[0] if row and row[0] else None

    def distinct_dates(self, table: str = "daily") -> list[str]:
        allowed = {"daily", "daily_basic", "moneyflow"}
        if table not in allowed:
            raise ValueError(table)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date"
            ).fetchall()
            return [r[0] for r in rows]

    def load_daily(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        codes: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """按需加载日线，禁止无过滤全表。"""
        cols = columns or [
            "ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "pct_chg", "vol", "amount",
        ]
        # 白名单列
        allowed = {
            "ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "change", "pct_chg", "vol", "amount",
        }
        cols = [c for c in cols if c in allowed]
        if "ts_code" not in cols:
            cols.insert(0, "ts_code")
        if "trade_date" not in cols:
            cols.insert(1, "trade_date")
        col_sql = ", ".join(cols)
        where = []
        params: list[Any] = []
        if start:
            where.append("trade_date >= ?")
            params.append(start)
        if end:
            where.append("trade_date <= ?")
            params.append(end)
        if codes:
            placeholders = ",".join("?" * len(codes))
            where.append(f"ts_code IN ({placeholders})")
            params.extend(codes)
        if not where:
            # 安全：无过滤时仅允许按最新单日
            end = self.max_trade_date("daily")
            if not end:
                return pd.DataFrame(columns=cols)
            where.append("trade_date = ?")
            params.append(end)
        sql = f"SELECT {col_sql} FROM daily WHERE " + " AND ".join(where)
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def load_stock_basic(self) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT * FROM stock_basic", conn)

    def load_daily_basic_asof(self, trade_date: str, codes: list[str] | None = None) -> pd.DataFrame:
        params: list[Any] = [trade_date]
        sql = "SELECT * FROM daily_basic WHERE trade_date = ?"
        if codes:
            ph = ",".join("?" * len(codes))
            sql += f" AND ts_code IN ({ph})"
            params.extend(codes)
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def load_scan_result(self, trade_date: str | None = None) -> pd.DataFrame:
        with self.connect() as conn:
            if trade_date:
                return pd.read_sql_query(
                    "SELECT * FROM scan_result WHERE trade_date = ? ORDER BY total_score DESC",
                    conn,
                    params=[trade_date],
                )
            row = conn.execute("SELECT MAX(trade_date) FROM scan_result").fetchone()
            if not row or not row[0]:
                return pd.DataFrame()
            return pd.read_sql_query(
                "SELECT * FROM scan_result WHERE trade_date = ? ORDER BY total_score DESC",
                conn,
                params=[row[0]],
            )

    def partition_fingerprint(self, dataset: str, trade_date: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT content_sha256 FROM dataset_partitions WHERE dataset=? AND trade_date=?",
                (dataset, trade_date),
            ).fetchone()
            return row[0] if row else None

    def upsert_partition(
        self,
        dataset: str,
        trade_date: str,
        row_count: int,
        content_sha256: str,
    ) -> None:
        with self.connect() as conn:
            prev = conn.execute(
                "SELECT revision FROM dataset_partitions WHERE dataset=? AND trade_date=?",
                (dataset, trade_date),
            ).fetchone()
            rev = int(prev[0]) + 1 if prev else 1
            from datetime import datetime

            conn.execute(
                """
                INSERT INTO dataset_partitions(dataset, trade_date, row_count, content_sha256, revision, ingested_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(dataset, trade_date) DO UPDATE SET
                  row_count=excluded.row_count,
                  content_sha256=excluded.content_sha256,
                  revision=excluded.revision,
                  ingested_at=excluded.ingested_at
                """,
                (dataset, trade_date, row_count, content_sha256, rev, datetime.now().isoformat(timespec="seconds")),
            )

    def compute_daily_day_hash(self, trade_date: str) -> tuple[int, str]:
        """对某日 daily 内容计算 (row_count, sha256)。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT ts_code, open, high, low, close, vol FROM daily WHERE trade_date=? ORDER BY ts_code",
                (trade_date,),
            ).fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}\n".encode())
        return len(rows), h.hexdigest()


def input_hash_for_scan(*, as_of: str, days: int, codes: list[str], config_hash: str, dataset_version: str) -> str:
    """完整代码集合参与哈希（排序后全量 join，禁止只取前 50）。"""
    codes_sorted = sorted(str(c) for c in codes)
    codes_blob = "\n".join(codes_sorted)
    codes_sha = hashlib.sha256(codes_blob.encode()).hexdigest()
    blob = json.dumps(
        {
            "as_of": as_of,
            "days": days,
            "codes_sha256": codes_sha,
            "n_codes": len(codes_sorted),
            "config_hash": config_hash,
            "dataset_version": dataset_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
