"""PIT 追加写：分块写入历史表 + 写入清单 + revision 分配。

契约（implementation P1.1）：
- 只追加：同一业务键的每次写入分配 revision = max(existing)+1；禁止 UPDATE/DELETE
  （历史表触发器兜底）。
- 每个分块一个事务（单事务行数由调用方按基准预算，建议 ≤5 万行）。
- 每次写入同步登记 raw_ingest_manifests（dataset, partition_key, source,
  available_at, row_count, content_sha256），供覆盖率/抽样核对。
- 时间统一 +08:00；缺字段由 PitRecord 校验拒绝。
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES as HISTORY_TABLES
from ab_screener.domain.data_point import (
    PitRecord,
    canonical_json,
    normalize_ts,
)

_TZ = ZoneInfo("Asia/Shanghai")

MAX_ROWS_PER_TX = 50_000


def _table_for(dataset: str) -> str:
    table = f"{dataset}_history"
    if table not in HISTORY_TABLES:
        raise ValueError(f"未知 PIT 数据集: {dataset}（历史表: {sorted(HISTORY_TABLES)}）")
    return table


def next_revision(conn: sqlite3.Connection, dataset: str, business_key: dict[str, str]) -> int:
    """同一业务键已有最大 revision + 1；无记录则 1。"""
    table = _table_for(dataset)
    key_cols = HISTORY_TABLES[table]
    where = " AND ".join(f"{c}=?" for c in key_cols)
    row = conn.execute(
        f"SELECT COALESCE(MAX(revision),0) FROM {table} WHERE {where}",
        tuple(str(business_key[c]) for c in key_cols),
    ).fetchone()
    return int(row[0] or 0) + 1


def build_records(
    dataset: str,
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
    available_at: Any,
    conn: sqlite3.Connection,
) -> list[PitRecord]:
    """把原始行转成 PIT 记录：分配 revision、补 content_hash、时间归一化。"""
    table = _table_for(dataset)
    key_cols = HISTORY_TABLES[table]
    available = normalize_ts(available_at)
    out: list[PitRecord] = []
    seen: dict[tuple[str, ...], int] = {}  # chunk 内已分配 revision（同键多行 → 递增）
    for raw in rows:
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"PIT 写入拒绝空行（{dataset}）")
        bkey = {c: str(raw[c]) for c in key_cols if raw.get(c) not in (None, "")}
        if len(bkey) != len(key_cols):
            missing = [c for c in key_cols if c not in bkey]
            raise ValueError(f"PIT 业务键缺失: {missing}（{dataset}）")
        key_tuple = tuple(bkey[c] for c in key_cols)
        if key_tuple in seen:
            # 同分区同业务键重复行（如 top_list 同日多 reason）：revision 递增，不冲突
            seen[key_tuple] += 1
        else:
            seen[key_tuple] = next_revision(conn, dataset, bkey)
        rev = seen[key_tuple]
        out.append(
            PitRecord(
                business_key=bkey,
                revision=rev,
                available_at=available,
                source=source,
                payload={k: v for k, v in raw.items() if k not in key_cols},
            )
        )
    return out


def write_chunk(
    conn: sqlite3.Connection,
    dataset: str,
    records: list[PitRecord],
    *,
    partition_key: str,
    source: str,
    available_at: Any,
) -> dict[str, Any]:
    """单个事务写入一批记录并登记清单。返回 {rows, manifest_id}。"""
    if not records:
        return {"rows": 0, "manifest_id": None}
    if len(records) > MAX_ROWS_PER_TX:
        raise ValueError(
            f"单事务行数 {len(records)} 超过预算 {MAX_ROWS_PER_TX}；请按分区切块"
        )
    table = _table_for(dataset)
    key_cols = HISTORY_TABLES[table]
    available = normalize_ts(available_at)

    now = datetime.now(_TZ).isoformat(timespec="seconds")
    payloads = [canonical_json(r.payload) for r in records]
    digest = hashlib.sha256(("\n".join(payloads)).encode("utf-8")).hexdigest()
    manifest_id = hashlib.sha256(
        f"{dataset}|{partition_key}|{available}|{source}|{digest}".encode()
    ).hexdigest()[:16]

    insert_cols = key_cols + ["revision", "available_at", "source", "content_hash", "payload_json"]
    placeholders = ",".join("?" * len(insert_cols))
    for r in records:
        conn.execute(
            f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})",
            (
                *(str(r.business_key[c]) for c in key_cols),
                r.revision,
                r.available_at,
                r.source,
                r.content_hash,
                canonical_json(r.payload),
            ),
        )
    conn.execute(
        "INSERT INTO raw_ingest_manifests (manifest_id, dataset, partition_key, source,"
        " available_at, row_count, content_sha256, ingested_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (manifest_id, dataset, partition_key, source, available, len(records), digest, now),
    )
    conn.commit()
    return {"rows": len(records), "manifest_id": manifest_id}


def write_plain(
    conn: sqlite3.Connection,
    dataset: str,
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
    available_at: Any,
    partition_key: str,
) -> dict[str, Any]:
    """便捷入口：build_records + write_chunk 一步完成。"""
    records = build_records(dataset, rows, source=source, available_at=available_at, conn=conn)
    return write_chunk(
        conn, dataset, records, partition_key=partition_key, source=source, available_at=available_at
    )
