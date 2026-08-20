"""研究产物仓库（P3.1）：产物登记 + 内容哈希防篡改核对。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")


class ArtifactError(RuntimeError):
    """产物登记/核对错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_artifact(
    conn: sqlite3.Connection,
    *,
    trial_id: str,
    artifact_type: str,
    path: str | Path,
    content_sha256: str | None = None,
) -> str:
    """登记产物；content_sha256 缺省按文件内容计算。"""
    p = Path(path)
    if not p.is_file():
        raise ArtifactError(f"产物文件不存在: {p}")
    checksum = content_sha256 or sha256_of_file(p)
    artifact_id = hashlib.sha256(
        f"{trial_id}|{artifact_type}|{p.name}|{checksum}".encode()
    ).hexdigest()[:16]
    existing = conn.execute(
        "SELECT 1 FROM research_artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if existing:
        return artifact_id
    conn.execute(
        "INSERT INTO research_artifacts (artifact_id, trial_id, artifact_type, path,"
        " content_sha256, created_at) VALUES (?,?,?,?,?,?)",
        (artifact_id, trial_id, artifact_type, str(p), checksum, _now()),
    )
    conn.commit()
    return artifact_id


def verify_artifact(path: str | Path, expected_sha256: str) -> bool:
    """内容核对（防篡改）：当前文件哈希 == 登记哈希。"""
    p = Path(path)
    if not p.is_file():
        return False
    return sha256_of_file(p) == expected_sha256


def artifacts_for_trial(
    conn: sqlite3.Connection, trial_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT artifact_id, trial_id, artifact_type, path, content_sha256, created_at"
        " FROM research_artifacts WHERE trial_id=? ORDER BY created_at",
        (trial_id,),
    ).fetchall()
    return [
        {"artifact_id": r[0], "trial_id": r[1], "artifact_type": r[2],
         "path": r[3], "content_sha256": r[4], "created_at": r[5]}
        for r in rows
    ]
