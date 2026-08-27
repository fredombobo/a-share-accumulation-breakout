"""可验证备份/恢复（P6.3）：online backup、清单、压缩与保留策略。"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

KEEP_BACKUPS = 7
MAX_BACKUP_AGE = timedelta(hours=24)
MANIFEST_SCHEMA = "ab-verified-backup-v3"


class BackupError(RuntimeError):
    """备份错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: str | Path, *, chunk: int = 4 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _database_checks(db_path: str | Path) -> dict[str, Any]:
    conn = sqlite3.connect(
        f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=30
    )
    try:
        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        foreign_key_rows = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
        table_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "integrity": integrity_rows,
        "foreign_key_violations": foreign_key_rows,
        "table_count": table_count,
        "ok": integrity_rows == ["ok"] and not foreign_key_rows,
    }


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(backup_path.name + ".manifest.json")


def _write_manifest(backup_path: Path, payload: dict[str, Any]) -> Path:
    manifest = {**payload}
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical(manifest).encode("utf-8")
    ).hexdigest()
    path = _manifest_path(backup_path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        return None
    signature = str(payload.get("manifest_sha256") or "")
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    expected = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    if not signature or signature != expected:
        return None
    backup = path.parent / str(payload.get("backup_file") or "")
    if not backup.is_file():
        return None
    if int(payload.get("size_bytes") or -1) != backup.stat().st_size:
        return None
    logical_sha256 = str(payload.get("logical_sha256") or "")
    if len(logical_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in logical_sha256
    ):
        return None
    if payload.get("integrity") != "ok" or int(payload.get("foreign_key_violations") or 0):
        return None
    return {**payload, "path": str(backup), "manifest_path": str(path)}


def list_verified_backups(backup_root: str | Path) -> list[dict[str, Any]]:
    """列出带有效自校验清单的备份；裸文件不计入七份验收。"""
    root = Path(backup_root)
    if not root.is_dir():
        return []
    items = [
        manifest
        for path in root.glob("backup_*.manifest.json")
        if (manifest := _read_manifest(path)) is not None
    ]
    return sorted(items, key=lambda item: str(item.get("verified_at") or ""))


def _unique_backup_path(root: Path, stamp: str, *, compressed: bool) -> Path:
    suffix = ".db.gz" if compressed else ".db"
    final = root / f"backup_{stamp}{suffix}"
    seq = 1
    while final.exists() or _manifest_path(final).exists():
        final = root / f"backup_{stamp}_{seq}{suffix}"
        seq += 1
    return final


def _compress(source: Path, target: Path) -> None:
    with source.open("rb") as src, gzip.open(target, "wb", compresslevel=3) as dst:
        shutil.copyfileobj(src, dst, length=4 << 20)


def create_backup(
    db_path: str | Path,
    backup_root: str | Path,
    *,
    compressed: bool = False,
) -> dict[str, Any]:
    """创建并验证一份备份；只有数据与清单都原子落盘后才算成功。"""
    source = Path(db_path).resolve()
    root = Path(backup_root).resolve()
    if not source.is_file():
        raise BackupError(f"源数据库不存在: {source}")
    if not root.is_dir():
        raise BackupError(f"备份根目录不可写: {root}")
    stamp = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    nonce = uuid.uuid4().hex
    tmp_db = root / f".backup_{stamp}_{nonce}.tmp.db"
    tmp_archive = root / f".backup_{stamp}_{nonce}.tmp.gz"
    final = _unique_backup_path(root, stamp, compressed=compressed)
    try:
        src = sqlite3.connect(str(source), timeout=30)
        dst = sqlite3.connect(str(tmp_db), timeout=30)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()

        checks = _database_checks(tmp_db)
        if not checks["ok"]:
            raise BackupError(f"备份完整性校验失败: {checks}")
        # SQLite online-backup API already creates one transactionally consistent
        # snapshot.  Hash that immutable logical database once.  The former Python
        # row-by-row JSON hash took longer than the 30-minute restore RTO on a
        # 13--16 GB production database while adding no protection beyond a full
        # byte-level digest.
        logical_sha256 = _sha256_file(tmp_db)
        logical_size_bytes = tmp_db.stat().st_size

        if compressed:
            _compress(tmp_db, tmp_archive)
            tmp_archive.replace(final)
        else:
            tmp_db.replace(final)
        verified_at = _now()
        archive_sha256 = _sha256_file(final)
        manifest_path = _write_manifest(
            final,
            {
                "schema": MANIFEST_SCHEMA,
                "backup_file": final.name,
                "archive_format": "gzip" if compressed else "sqlite",
                "size_bytes": final.stat().st_size,
                "logical_size_bytes": logical_size_bytes,
                "archive_sha256": archive_sha256,
                "logical_sha256": logical_sha256,
                "source": {
                    "name": source.name,
                    "size_bytes": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns,
                },
                "table_count": checks["table_count"],
                "integrity": "ok",
                "foreign_key_violations": 0,
                "created_at": verified_at,
                "verified_at": verified_at,
                "tool_version": "backup-v3",
            },
        )
        prune_old_backups(root)
        return {
            "path": str(final),
            "manifest_path": str(manifest_path),
            "size_bytes": final.stat().st_size,
            "logical_size_bytes": logical_size_bytes,
            "archive_sha256": archive_sha256,
            "tables": checks["table_count"],
            "created_at": verified_at,
            "verified_at": verified_at,
            "compressed": compressed,
            "logical_sha256": logical_sha256,
        }
    except Exception:
        final.unlink(missing_ok=True)
        _manifest_path(final).unlink(missing_ok=True)
        raise
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_archive.unlink(missing_ok=True)
        for suffix in ("-shm", "-wal"):
            Path(str(tmp_db) + suffix).unlink(missing_ok=True)


def verify_backup(backup_path: str | Path) -> dict[str, Any]:
    """对既有 SQLite 备份做完整验证并补写不可变清单。"""
    backup = Path(backup_path).resolve()
    if not backup.is_file() or backup.suffix.lower() != ".db":
        raise BackupError("既有备份验证目前只接受 .db 文件")
    wal = Path(str(backup) + "-wal")
    if wal.is_file() and wal.stat().st_size:
        raise BackupError("既有备份仍含未归并 WAL，拒绝按单文件验证")
    checks = _database_checks(backup)
    if not checks["ok"]:
        raise BackupError(f"备份完整性校验失败: {checks}")
    verified_at = _now()
    digest = _sha256_file(backup)
    manifest_path = _write_manifest(
        backup,
        {
            "schema": MANIFEST_SCHEMA,
            "backup_file": backup.name,
            "archive_format": "sqlite",
            "size_bytes": backup.stat().st_size,
            "logical_size_bytes": backup.stat().st_size,
            "archive_sha256": digest,
            "logical_sha256": digest,
            "source": {"name": "legacy-online-backup", "identity": "historical"},
            "table_count": checks["table_count"],
            "integrity": "ok",
            "foreign_key_violations": 0,
            "created_at": datetime.fromtimestamp(
                backup.stat().st_mtime, _TZ
            ).isoformat(timespec="seconds"),
            "verified_at": verified_at,
            "tool_version": "backup-v3-retrofit",
        },
    )
    return {
        "path": str(backup),
        "manifest_path": str(manifest_path),
        "archive_sha256": digest,
        "tables": checks["table_count"],
        "logical_sha256": digest,
        "verified_at": verified_at,
    }


def restore_verified_backup(
    backup_path: str | Path,
    restore_to: str | Path,
) -> dict[str, Any]:
    """将一份清单有效的备份恢复到全新目标，并逐项复验。

    目标已存在时拒绝执行，避免误覆盖生产库或上一次演练结果。
    """
    backup = Path(backup_path).resolve()
    target = Path(restore_to).resolve()
    manifest = _read_manifest(_manifest_path(backup))
    if manifest is None:
        raise BackupError("备份清单无效或文件已被篡改")
    if target.exists():
        raise BackupError(f"恢复目标已存在，拒绝覆盖: {target}")
    if not target.parent.is_dir():
        raise BackupError(f"恢复目标目录不存在: {target.parent}")
    if backup == target:
        raise BackupError("恢复目标不得与备份源相同")
    started = time.monotonic()
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        actual_sha256 = _sha256_file(backup)
        if actual_sha256 != manifest.get("archive_sha256"):
            raise BackupError("备份文件 SHA-256 与清单不一致")
        archive_format = str(manifest.get("archive_format") or "")
        if archive_format == "gzip":
            with gzip.open(backup, "rb") as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=4 << 20)
        elif archive_format == "sqlite":
            shutil.copy2(backup, tmp)
        else:
            raise BackupError(f"不支持的备份格式: {archive_format}")
        checks = _database_checks(tmp)
        if not checks["ok"]:
            raise BackupError(f"恢复库完整性校验失败: {checks}")
        logical_sha256 = _sha256_file(tmp)
        if logical_sha256 != manifest.get("logical_sha256"):
            raise BackupError("恢复库逻辑 SHA-256 与备份清单不一致")
        tmp.replace(target)
        elapsed = round(time.monotonic() - started, 3)
        return {
            "status": "PASS",
            "backup_path": str(backup),
            "manifest_path": str(_manifest_path(backup)),
            "restore_path": str(target),
            "archive_sha256": actual_sha256,
            "integrity": "ok",
            "foreign_key_violations": 0,
            "logical_sha256": logical_sha256,
            "logical_sha256_match": True,
            # Backward-compatible response field for existing gate consumers.
            # A full logical database byte hash is stronger and substantially
            # faster than the retired row-serialization comparison.
            "table_hashes_match": True,
            "duration_sec": elapsed,
            "rto_target_sec": 1800,
            "rto_pass": elapsed <= 1800,
            "restored_at": _now(),
        }
    finally:
        tmp.unlink(missing_ok=True)


def prune_old_backups(backup_root: str | Path, keep: int = KEEP_BACKUPS) -> list[str]:
    """只清理有有效清单的旧备份；裸历史文件和唯一备份绝不删除。"""
    verified = list_verified_backups(backup_root)
    if len(verified) <= keep:
        return []
    removed: list[str] = []
    for item in verified[:-keep]:
        if len(verified) - len(removed) <= 1:
            break
        backup = Path(str(item["path"]))
        manifest = Path(str(item["manifest_path"]))
        backup.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        removed.append(str(backup))
    return removed


def latest_backup(backup_root: str | Path) -> dict[str, Any] | None:
    verified = list_verified_backups(backup_root)
    if not verified:
        return None
    latest = verified[-1]
    verified_at = datetime.fromisoformat(str(latest["verified_at"]))
    return {
        "path": latest["path"],
        "manifest_path": latest["manifest_path"],
        "created_at": latest.get("created_at"),
        "verified_at": latest["verified_at"],
        "age_hours": round(
            (datetime.now(_TZ) - verified_at).total_seconds() / 3600, 2
        ),
        "size_bytes": latest["size_bytes"],
        "logical_size_bytes": latest.get("logical_size_bytes"),
        "archive_format": latest.get("archive_format"),
        "archive_sha256": latest.get("archive_sha256"),
    }


def backup_ok(backup_root: str | Path) -> dict[str, Any]:
    """验收：至少七份带有效清单的备份，且最近验证成功时间小于 24 小时。"""
    root = Path(backup_root)
    latest = latest_backup(root)
    if latest is None:
        bare_count = len(list(root.glob("backup_*.db"))) if root.is_dir() else 0
        return {
            "ok": False,
            "count": 0,
            "required": KEEP_BACKUPS,
            "reason": "无带有效校验清单的备份",
            "unverified_files": bare_count,
        }
    count = len(list_verified_backups(root))
    ok = count >= KEEP_BACKUPS and latest["age_hours"] < 24
    reason = ""
    if count < KEEP_BACKUPS:
        reason = f"已验证备份 {count}/{KEEP_BACKUPS}"
    elif latest["age_hours"] >= 24:
        reason = "最近验证成功备份已超过 24 小时"
    return {
        "ok": ok,
        "count": count,
        "latest_age_hours": latest["age_hours"],
        "required": KEEP_BACKUPS,
        "max_age_hours": 24,
        "reason": reason,
        "latest": latest,
    }
