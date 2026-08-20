"""市场情报仓库：快照协调 + 分区清单（只读）。

契约（implementation P1.4）：
- 信息读取与扫描引用同一 snapshot ID：`snapshot_fingerprint` 由分区最新
  manifest（content_sha256 + row_count）派生；分区修订后指纹变化 → 缓存按
  manifest 失效。
- 本仓库只读：不创建信号、不产生订单、不写数据库。
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES as HISTORY_TABLES


@dataclass(frozen=True)
class PartitionManifest:
    dataset: str
    partition_key: str
    manifest_id: str
    content_sha256: str
    row_count: int
    available_at: str
    ingested_at: str


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)


def latest_manifest(db_path: str | Path, dataset: str, partition_key: str) -> PartitionManifest | None:
    table = f"{dataset}_history"
    if table not in HISTORY_TABLES:
        raise ValueError(f"未知 PIT 数据集: {dataset}")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT dataset, partition_key, manifest_id, content_sha256, row_count,"
            " available_at, ingested_at FROM raw_ingest_manifests"
            " WHERE dataset=? AND partition_key=?"
            " ORDER BY rowid DESC LIMIT 1",
            (dataset, partition_key),
        ).fetchone()
    if row is None:
        return None
    return PartitionManifest(*row)


def snapshot_fingerprint(db_path: str | Path, dataset: str, partition_key: str) -> str:
    """分区内容的稳定快照指纹（manifest 变更 → 指纹变化 → 缓存失效）。"""
    manifest = latest_manifest(db_path, dataset, partition_key)
    if manifest is None:
        return "NO_MANIFEST"
    blob = f"{manifest.dataset}|{manifest.partition_key}|{manifest.content_sha256}|{manifest.row_count}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dataset_status(db_path: str | Path) -> dict[str, Any]:
    """各数据集的来源状态：最新 manifest 数量、覆盖分区、清单摘要。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT dataset, COUNT(*) AS partitions, COALESCE(SUM(row_count),0),"
            " MAX(ingested_at) FROM raw_ingest_manifests"
            " GROUP BY dataset ORDER BY dataset"
        ).fetchall()
    out: dict[str, Any] = {}
    for dataset, partitions, rows_total, last_ingested in rows:
        out[dataset] = {
            "partitions": int(partitions),
            "rows": int(rows_total),
            "last_ingested_at": last_ingested,
        }
    return out
