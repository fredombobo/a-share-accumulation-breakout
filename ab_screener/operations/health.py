"""系统健康（P6.3）：build/config/data/DB/WAL/磁盘/DAG/备份/扫描/对账/端口。"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.operations.backup import backup_ok


def _db_fingerprint(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return {"ok": False, "reason": "DB 缺失"}
    size = path.stat().st_size
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        wal_size = (Path(str(path) + "-wal").stat().st_size
                    if Path(str(path) + "-wal").exists() else 0)
    return {
        "ok": integrity == "ok",
        "size_bytes": size,
        "wal_bytes": wal_size,
        "integrity": integrity,
    }


def _disk_status(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    usage = shutil.disk_usage(path.anchor or path.parent)
    free_gb = usage.free / 1e9
    return {"free_gb": round(free_gb, 2), "ok": free_gb > 1.0}


def system_health(
    db_path: str | Path,
    backup_root: str | Path,
    *,
    build_version: str = "",
    config_hash: str = "",
    port: int = 8001,
) -> dict[str, Any]:
    """聚合健康状态；任何关键项 FAIL → overall FAIL。"""
    db = _db_fingerprint(db_path)
    disk = _disk_status(db_path)
    backup = backup_ok(backup_root)
    issues: list[str] = []
    if not db["ok"]:
        issues.append("DB 完整性失败")
    if not disk["ok"]:
        issues.append("磁盘空间不足")
    if not backup["ok"]:
        issues.append(f"备份不满足要求: {backup.get('reason')}")
    return {
        "status": "FAIL" if issues else "PASS",
        "issues": issues,
        "build_version": build_version,
        "config_hash": config_hash,
        "port": port,
        "database": db,
        "disk": disk,
        "backup": backup,
        "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
