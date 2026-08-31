"""龙虎榜增量抓取：交易日历补洞、幂等落库、失败不伪装成成功空数据。"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from typing import Any

from ab_screener.data.adapters.lhb_sources import FetchResult, rows_content_sha256, with_fallback
from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES
from ab_screener.data.migration_intents.lhb_tracking_v2 import LHB_PIT_HISTORY_TABLES
from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.lhb_contracts import parse_trade_date, require_available_at, validate_manifest_row

_TABLES = {**ALL_HISTORY_TABLES, **LHB_PIT_HISTORY_TABLES}
_DONE_STATUSES = frozenset({"COMPLETE", "VALID_EMPTY"})


def manifest_id_for(result: FetchResult) -> str:
    blob = "|".join(
        (
            result.dataset,
            result.partition_key,
            result.source,
            result.source_status,
            result.content_sha256,
            result.error_reason or "",
        )
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def missing_trade_dates(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    source: str,
    calendar_dates: Iterable[str],
) -> list[str]:
    """按交易日历补洞，不是 MAX(trade_date)。失败/未发布/降级分区仍待抓。"""
    calendar = [parse_trade_date(d) for d in calendar_dates]
    rows = conn.execute(
        "SELECT partition_key, source_status FROM lhb_ingest_manifests"
        " WHERE dataset=? AND source=?",
        (dataset, source),
    ).fetchall()
    done = {str(k) for k, status in rows if status in _DONE_STATUSES}
    return [d for d in calendar if d not in done]


def _history_table(dataset: str) -> tuple[str, list[str]]:
    if dataset == "official_raw":
        table = "lhb_official_raw_history"
    else:
        table = f"{dataset}_history"
    if table not in _TABLES:
        raise ValueError(f"未知 LHB 数据集: {dataset}")
    return table, _TABLES[table]


def _next_revision(conn: sqlite3.Connection, table: str, key_cols: list[str], key: dict[str, str]) -> int:
    where = " AND ".join(f"{c}=?" for c in key_cols)
    row = conn.execute(
        f"SELECT COALESCE(MAX(revision),0) FROM {table} WHERE {where}",
        tuple(key[c] for c in key_cols),
    ).fetchone()
    return int(row[0] or 0) + 1


def persist_fetch(conn: sqlite3.Connection, result: FetchResult) -> dict[str, Any]:
    validate_manifest_row(source_status=result.source_status, row_count=result.row_count)
    available = require_available_at(result.available_at)
    mid = manifest_id_for(result)
    existing = conn.execute(
        "SELECT manifest_id, content_sha256 FROM lhb_ingest_manifests"
        " WHERE dataset=? AND partition_key=? AND source=? AND content_sha256=?"
        " AND source_status=? AND ifnull(error_reason,'')=?",
        (
            result.dataset,
            result.partition_key,
            result.source,
            result.content_sha256,
            result.source_status,
            result.error_reason or "",
        ),
    ).fetchone()
    if existing:
        return {
            "skipped": True,
            "manifest_id": existing[0],
            "rows_written": 0,
            "source_status": result.source_status,
        }
    rev_row = conn.execute(
        "SELECT COALESCE(MAX(revision),0) FROM lhb_ingest_manifests"
        " WHERE dataset=? AND partition_key=? AND source=?",
        (result.dataset, result.partition_key, result.source),
    ).fetchone()
    revision = int(rev_row[0] or 0) + 1
    rows_written = 0
    write_rows = result.source_status in {"COMPLETE", "DEGRADED"}
    if write_rows and result.rows:
        table, key_cols = _history_table(result.dataset)
        for raw in result.rows:
            bkey = {c: str(raw[c]) for c in key_cols}
            rev = _next_revision(conn, table, key_cols, bkey)
            payload = {k: v for k, v in raw.items() if k not in key_cols}
            conn.execute(
                f"INSERT INTO {table} ({', '.join(key_cols)}, revision, available_at, source,"
                " content_hash, payload_json) VALUES ({})".format(
                    ",".join("?" * (len(key_cols) + 5))
                ),
                (
                    *[bkey[c] for c in key_cols],
                    rev,
                    available,
                    result.source,
                    hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16],
                    canonical_json(payload),
                ),
            )
            rows_written += 1
    has_raw = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_ingest_manifests'"
    ).fetchone()
    if has_raw:
        conn.execute(
            "INSERT INTO raw_ingest_manifests (manifest_id, dataset, partition_key, source,"
            " available_at, row_count, content_sha256, ingested_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(manifest_id) DO NOTHING",
            (
                mid,
                result.dataset,
                result.partition_key,
                result.source,
                available,
                result.row_count,
                result.content_sha256,
                available,
            ),
        )
    conn.execute(
        "INSERT INTO lhb_ingest_manifests (manifest_id, dataset, partition_key, source, revision,"
        " source_status, row_count, content_sha256, error_reason, available_at, ingested_at,"
        " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            mid,
            result.dataset,
            result.partition_key,
            result.source,
            revision,
            result.source_status,
            result.row_count,
            result.content_sha256,
            result.error_reason,
            available,
            available,
            canonical_json(result.summary),
        ),
    )
    conn.commit()
    return {
        "skipped": False,
        "manifest_id": mid,
        "rows_written": rows_written,
        "source_status": result.source_status,
    }


def ingest_with_fallback(
    conn: sqlite3.Connection,
    primary: FetchResult,
    secondary: FetchResult | None = None,
) -> dict[str, Any]:
    chosen = with_fallback(primary, secondary) if secondary is not None else primary
    return persist_fetch(conn, chosen)


def empty_sha() -> str:
    return rows_content_sha256([])
