"""系统健康（P6.3）：build/config/data/DB/WAL/磁盘/DAG/备份/扫描/对账/端口。

快速健康（GET /api/v2/system/health）不得在热路径执行 PRAGMA integrity_check
（16GB 库需数分钟）；深检由 scripts/check_db_integrity.py 离线产出证书，
接口只读取匹配当前 DB fingerprint 的最新证书。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.operations.backup import backup_ok

_INTEGRITY_REPORT = Path("runtime") / "v2" / "integrity_report.json"


def db_fingerprint(path: Path) -> str:
    """轻量 DB 指纹：路径名 + size + mtime（与 check_db_integrity.py 同口径）。"""
    st = path.stat()
    return hashlib.sha256(f"{path.name}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _deep_check_status(db_path: Path, fingerprint: str) -> dict[str, Any]:
    """读取离线深检证书；匹配当前 fingerprint 则 PASS，否则 STALE/MISSING。"""
    report = _INTEGRITY_REPORT
    if not report.is_file():
        return {"status": "MISSING", "reason": "无深检证书"}
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "MISSING", "reason": "深检证书不可读"}
    if data.get("fingerprint") != fingerprint:
        return {"status": "STALE", "reason": "证书 fingerprint 与当前 DB 不一致"}
    if data.get("integrity") != "ok":
        return {"status": "FAIL", "integrity": data.get("integrity"), "reason": "深检完整性失败"}
    return {
        "status": "PASS",
        "finished_at": data.get("finished_at"),
        "duration_sec": data.get("duration_sec"),
        "tables": data.get("tables"),
        "sha256": data.get("sha256", "")[:16],
    }


def _db_fingerprint(db_path: str | Path) -> dict[str, Any]:
    """快速检查：只读 schema/version/latest date/WAL + 既有完整性证书，绝不跑 integrity_check。"""
    path = Path(db_path)
    if not path.is_file():
        return {"ok": False, "reason": "DB 缺失"}
    size = path.stat().st_size
    fingerprint = db_fingerprint(path)
    wal_size = (Path(str(path) + "-wal").stat().st_size
                if Path(str(path) + "-wal").exists() else 0)
    schema_version: str | None = None
    latest_date: str | None = None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            try:
                row = conn.execute("SELECT value FROM schema_version WHERE id='schema_version'").fetchone()
                schema_version = row[0] if row else None
            except Exception:  # noqa: BLE001
                schema_version = None
            try:
                latest_date = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
            except Exception:  # noqa: BLE001
                latest_date = None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"DB 不可读: {str(exc)[:80]}"}
    deep_check = _deep_check_status(path, fingerprint)
    return {
        "ok": True,
        "size_bytes": size,
        "wal_bytes": wal_size,
        "fingerprint": fingerprint,
        "schema_version": schema_version,
        "latest_date": latest_date,
        "deep_check": deep_check,
    }


def _disk_status(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    usage = shutil.disk_usage(path.anchor or path.parent)
    free_gb = usage.free / 1e9
    return {"free_gb": round(free_gb, 2), "ok": free_gb > 1.0}


def system_health(
    db_path: str | Path,
    backup_root: str | Path | None,
    *,
    build_version: str = "",
    config_hash: str = "",
    port: int = 8001,
) -> dict[str, Any]:
    """聚合健康状态；任何关键项 FAIL → overall FAIL。快速路径（无 integrity_check）。

    backup_root 为 None 时备份状态明确 BACKUP_ROOT_UNCONFIGURED（不悄悄当通过）。
    """
    db = _db_fingerprint(db_path)
    disk = _disk_status(db_path)
    backup = backup_ok(backup_root) if backup_root is not None else {
        "status": "BACKUP_ROOT_UNCONFIGURED", "ok": False, "reason": "AB_BACKUP_ROOT 未配置",
    }
    issues: list[str] = []
    if not db["ok"]:
        issues.append("DB 快速检查失败")
    if db.get("deep_check", {}).get("status") in ("FAIL",):
        issues.append("DB 深检完整性失败")
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
