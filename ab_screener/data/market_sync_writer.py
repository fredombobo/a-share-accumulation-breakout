"""Atomic canonical + PIT writes for revisable market-data partitions.

The legacy synchronizer used to update ``daily``/``daily_basic``/``moneyflow``
without appending their PIT history.  This writer makes one fetched partition
atomic: preserve an untracked canonical value, append a source revision only
when content changed, then update the canonical projection in the same
``BEGIN IMMEDIATE`` transaction.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ab_screener.domain.data_point import canonical_json, content_hash_for, normalize_ts

_TZ = ZoneInfo("Asia/Shanghai")
_KEY_COLUMNS = ("ts_code", "trade_date")
_DATA_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily": (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ),
    "daily_basic": (
        "close",
        "pe",
        "pb",
        "ps_ttm",
        "dp",
        "total_mv",
        "circ_mv",
        "turnover_rate",
        "volume_ratio",
    ),
    "moneyflow": (
        "buy_elg_amount",
        "buy_elg_vol",
        "buy_lg_amount",
        "buy_lg_vol",
        "buy_md_amount",
        "buy_md_vol",
        "buy_sm_amount",
        "buy_sm_vol",
        "net_mf_amount",
        "net_mf_vol",
        "sell_elg_amount",
        "sell_elg_vol",
        "sell_lg_amount",
        "sell_lg_vol",
        "sell_md_amount",
        "sell_md_vol",
        "sell_sm_amount",
        "sell_sm_vol",
    ),
}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _payload(row: dict[str, Any], dataset: str) -> dict[str, Any]:
    payload: dict[str, float | None] = {}
    for column in _DATA_COLUMNS[dataset]:
        value = _clean(row.get(column))
        if value is None:
            payload[column] = None
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{dataset}.{column} 不是有效数值: {value!r}"
            ) from exc
        if math.isnan(numeric) or math.isinf(numeric):
            payload[column] = None
        else:
            payload[column] = 0.0 if numeric == 0 else numeric
    return payload


def _effective_at(trade_date: str) -> str:
    parsed = datetime.strptime(trade_date, "%Y%m%d")
    return parsed.replace(hour=15, tzinfo=_TZ).isoformat(timespec="seconds")


def _existing_canonical(
    conn: sqlite3.Connection,
    dataset: str,
    trade_date: str,
) -> dict[str, dict[str, Any]]:
    columns = [*_KEY_COLUMNS, *_DATA_COLUMNS[dataset]]
    if dataset == "daily":
        columns.extend(["available_at", "source", "revision"])
    rows = conn.execute(
        f"SELECT {','.join(columns)} FROM {dataset} WHERE trade_date=?",
        (trade_date,),
    ).fetchall()
    return {str(row[0]): dict(zip(columns, row, strict=True)) for row in rows}


def _latest_history(
    conn: sqlite3.Connection,
    dataset: str,
    trade_date: str,
) -> dict[str, dict[str, Any]]:
    table = f"{dataset}_history"
    rows = conn.execute(
        f"SELECT ts_code,revision,available_at,source,content_hash FROM {table} "
        "WHERE trade_date=? ORDER BY ts_code,revision",
        (trade_date,),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for ts_code, revision, available_at, source, content_hash in rows:
        latest[str(ts_code)] = {
            "revision": int(revision),
            "available_at": str(available_at),
            "source": str(source),
            "content_hash": str(content_hash),
        }
    return latest


def _insert_history(
    conn: sqlite3.Connection,
    dataset: str,
    rows: list[dict[str, Any]],
) -> None:
    table = f"{dataset}_history"
    conn.executemany(
        f"INSERT INTO {table} "
        "(ts_code,trade_date,revision,available_at,source,content_hash,payload_json) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (
                row["ts_code"],
                row["trade_date"],
                row["revision"],
                row["available_at"],
                row["source"],
                row["content_hash"],
                canonical_json(row["payload"]),
            )
            for row in rows
        ],
    )


def _insert_manifest(
    conn: sqlite3.Connection,
    dataset: str,
    partition_key: str,
    source: str,
    available_at: str,
    rows: list[dict[str, Any]],
) -> str | None:
    if not rows:
        return None
    digest = hashlib.sha256(
        "\n".join(
            canonical_json(
                {
                    "ts_code": row["ts_code"],
                    "trade_date": row["trade_date"],
                    "revision": row["revision"],
                    "payload": row["payload"],
                }
            )
            for row in sorted(
                rows,
                key=lambda item: (
                    str(item["ts_code"]),
                    str(item["trade_date"]),
                    int(item["revision"]),
                ),
            )
        ).encode()
    ).hexdigest()
    manifest_id = hashlib.sha256(
        f"{dataset}|{partition_key}|{source}|{available_at}|{digest}".encode()
    ).hexdigest()[:16]
    conn.execute(
        "INSERT INTO raw_ingest_manifests "
        "(manifest_id,dataset,partition_key,source,available_at,row_count,"
        "content_sha256,ingested_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            manifest_id,
            dataset,
            partition_key,
            source,
            available_at,
            len(rows),
            digest,
            datetime.now(_TZ).isoformat(timespec="seconds"),
        ),
    )
    return manifest_id


def _upsert_canonical(
    conn: sqlite3.Connection,
    dataset: str,
    source_rows: list[dict[str, Any]],
) -> int:
    if not source_rows:
        return 0
    columns = [*_KEY_COLUMNS, *_DATA_COLUMNS[dataset]]
    if dataset == "daily":
        columns.extend(
            ["effective_at", "available_at", "ingested_at", "source", "revision", "is_legacy"]
        )
    placeholders = ",".join("?" for _ in columns)
    assignments = ",".join(
        f"{column}=excluded.{column}" for column in columns if column not in _KEY_COLUMNS
    )
    conn.executemany(
        f"INSERT INTO {dataset} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ts_code,trade_date) DO UPDATE SET {assignments}",
        [tuple(row.get(column) for column in columns) for row in source_rows],
    )
    return len(source_rows)


def market_partition_pit_status(
    db_path: str | Path,
    dataset: str,
    trade_date: str,
) -> dict[str, Any]:
    """Compare one canonical partition with its latest append-only revision."""
    if dataset not in _DATA_COLUMNS:
        raise ValueError(f"不支持的市场数据集: {dataset}")
    path = Path(db_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        canonical = _existing_canonical(conn, dataset, trade_date)
        latest = _latest_history(conn, dataset, trade_date)
        canonical_codes = set(canonical)
        history_codes = set(latest)
        missing = sorted(canonical_codes - history_codes)
        orphan = sorted(history_codes - canonical_codes)
        mismatched = sorted(
            ts_code
            for ts_code in canonical_codes & history_codes
            if content_hash_for(_payload(canonical[ts_code], dataset))
            != latest[ts_code]["content_hash"]
        )
        metadata_mismatches: list[str] = []
        if dataset == "daily":
            metadata_mismatches = sorted(
                ts_code
                for ts_code in canonical_codes & history_codes
                if int(canonical[ts_code].get("revision") or 0)
                != int(latest[ts_code]["revision"])
                or str(canonical[ts_code].get("available_at") or "")
                != latest[ts_code]["available_at"]
                or str(canonical[ts_code].get("source") or "")
                != latest[ts_code]["source"]
            )
        ok = bool(canonical) and not (
            missing or orphan or mismatched or metadata_mismatches
        )
        return {
            "dataset": dataset,
            "trade_date": trade_date,
            "passed": ok,
            "canonical_rows": len(canonical),
            "history_rows": len(latest),
            "missing_history": len(missing),
            "orphan_history": len(orphan),
            "content_mismatches": len(mismatched),
            "metadata_mismatches": len(metadata_mismatches),
            "samples": {
                "missing_history": missing[:5],
                "orphan_history": orphan[:5],
                "content_mismatches": mismatched[:5],
                "metadata_mismatches": metadata_mismatches[:5],
            },
        }
    finally:
        conn.close()


def reconcile_market_partition(
    db_path: str | Path,
    dataset: str,
    frame: pd.DataFrame,
    *,
    trade_date: str,
    available_at: str | datetime,
    source: str = "tushare",
) -> dict[str, Any]:
    """Persist one market partition without losing or duplicating revisions."""
    if dataset not in _DATA_COLUMNS:
        raise ValueError(f"不支持的市场数据集: {dataset}")
    if frame is None or frame.empty:
        raise ValueError(f"{dataset}/{trade_date} 数据源返回空")
    available = normalize_ts(available_at)
    records = [
        {key: _clean(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]
    if any(str(row.get("trade_date") or "") != trade_date for row in records):
        raise ValueError(f"{dataset}/{trade_date} 包含其它交易日")
    codes = [str(row.get("ts_code") or "") for row in records]
    if not all(codes) or len(codes) != len(set(codes)):
        raise ValueError(f"{dataset}/{trade_date} 标的代码缺失或重复")

    conn = sqlite3.connect(str(Path(db_path)), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        canonical = _existing_canonical(conn, dataset, trade_date)
        latest = _latest_history(conn, dataset, trade_date)
        recovered: list[dict[str, Any]] = []
        appended: list[dict[str, Any]] = []
        canonical_updates: list[dict[str, Any]] = []

        for raw, ts_code in zip(records, codes, strict=True):
            current = canonical.get(ts_code)
            state = latest.get(ts_code)
            current_hash = content_hash_for(_payload(current, dataset)) if current else None

            # Preserve a canonical value that a legacy sync wrote without PIT.
            if current and (state is None or state["content_hash"] != current_hash):
                revision = int(state["revision"]) + 1 if state else 1
                recovered_available = (
                    str(current.get("available_at") or available)
                    if dataset == "daily"
                    else available
                )
                recovered_row = {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "revision": revision,
                    "available_at": normalize_ts(recovered_available),
                    "source": f"canonical_recovery:{current.get('source') or 'legacy_sync'}",
                    "payload": _payload(current, dataset),
                    "content_hash": current_hash,
                }
                recovered.append(recovered_row)
                state = {
                    "revision": revision,
                    "available_at": recovered_row["available_at"],
                    "source": recovered_row["source"],
                    "content_hash": current_hash,
                }

            source_payload = _payload(raw, dataset)
            source_hash = content_hash_for(source_payload)
            source_provenance_missing = bool(
                state
                and str(state["source"]).startswith("canonical_recovery:")
            )
            if (
                state is None
                or state["content_hash"] != source_hash
                or source_provenance_missing
            ):
                revision = int(state["revision"]) + 1 if state else 1
                source_record = {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "revision": revision,
                    "available_at": available,
                    "source": source,
                    "payload": source_payload,
                    "content_hash": source_hash,
                }
                appended.append(source_record)
                state = {
                    "revision": revision,
                    "available_at": available,
                    "source": source,
                    "content_hash": source_hash,
                }

            metadata_changed = bool(
                dataset == "daily"
                and current
                and (
                    int(current.get("revision") or 0) != int(state["revision"])
                    or str(current.get("available_at") or "") != state["available_at"]
                    or str(current.get("source") or "") != state["source"]
                )
            )
            if current_hash != source_hash or metadata_changed:
                projection = {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    **source_payload,
                }
                if dataset == "daily":
                    projection.update(
                        {
                            "effective_at": _effective_at(trade_date),
                            "available_at": state["available_at"],
                            "ingested_at": available,
                            "source": state["source"],
                            "revision": state["revision"],
                            "is_legacy": 0,
                        }
                    )
                canonical_updates.append(projection)

        _insert_history(conn, dataset, recovered)
        _insert_history(conn, dataset, appended)
        recovery_manifest = _insert_manifest(
            conn,
            dataset,
            trade_date,
            "canonical_recovery",
            available,
            recovered,
        )
        source_manifest = _insert_manifest(
            conn, dataset, trade_date, source, available, appended
        )
        updated = _upsert_canonical(conn, dataset, canonical_updates)
        conn.commit()
        return {
            "dataset": dataset,
            "trade_date": trade_date,
            "source_rows": len(records),
            "recovered_revisions": len(recovered),
            "appended_revisions": len(appended),
            "canonical_updated": updated,
            "unchanged": len(records) - updated,
            "manifest_ids": [
                manifest
                for manifest in (recovery_manifest, source_manifest)
                if manifest is not None
            ],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
