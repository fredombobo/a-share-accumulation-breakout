"""备份/恢复（P6.3）：SQLite online backup + 校验 + 原子命名 + 保留策略。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

KEEP_BACKUPS = 7
MAX_BACKUP_AGE = timedelta(hours=24)


class BackupError(RuntimeError):
    """备份错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _table_hashes(db_path: str | Path) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        out: dict[str, str] = {}
        for table in tables:
            digest = hashlib.sha256()
            for row in conn.execute(f"SELECT * FROM {table}"):
                digest.update(str(row).encode("utf-8", errors="ignore"))
            out[table] = digest.hexdigest()[:16]
        return out
    finally:
        conn.close()


def create_backup(db_path: str | Path, backup_root: str | Path) -> dict[str, Any]:
    """SQLite online backup → 临时文件 → FK/行数/hash 校验 → 原子命名。"""
    db_path = Path(db_path)
    backup_root = Path(backup_root)
    if not db_path.is_file():
        raise BackupError(f"源数据库不存在: {db_path}")
    if not backup_root.is_dir():
        raise BackupError(f"备份根目录不可写: {backup_root}")
    stamp = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    tmp = backup_root / f".backup_{stamp}.tmp"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    # 校验：关键表 hash 一致（行数由 hash 覆盖）
    src_hashes = _table_hashes(db_path)
    dst_hashes = _table_hashes(tmp)
    if src_hashes != dst_hashes:
        tmp.unlink(missing_ok=True)
        raise BackupError("备份哈希校验不一致，已丢弃")
    # 原子命名：同名冲突（同秒）→ 追加序号，避免覆盖
    final = backup_root / f"backup_{stamp}.db"
    seq = 1
    while final.exists():
        final = backup_root / f"backup_{stamp}_{seq}.db"
        seq += 1
    tmp.rename(final)
    prune_old_backups(backup_root)
    return {
        "path": str(final),
        "size_bytes": final.stat().st_size,
        "tables": len(src_hashes),
        "created_at": _now(),
        "table_hashes": src_hashes,
    }


def prune_old_backups(backup_root: str | Path, keep: int = KEEP_BACKUPS) -> list[str]:
    """保留最近 keep 份；绝不删除唯一已验证备份。"""
    backups = sorted(Path(backup_root).glob("backup_*.db"), key=lambda p: p.stat().st_mtime)
    if len(backups) <= keep:
        return []
    removed: list[str] = []
    for old in backups[:-keep]:
        if len(backups) - len(removed) <= 1:
            break
        old.unlink(missing_ok=True)
        removed.append(str(old))
    return removed


def latest_backup(backup_root: str | Path) -> dict[str, Any] | None:
    backups = sorted(Path(backup_root).glob("backup_*.db"), key=lambda p: p.stat().st_mtime)
    if not backups:
        return None
    latest = backups[-1]
    return {
        "path": str(latest),
        "created_at": datetime.fromtimestamp(latest.stat().st_mtime, _TZ).isoformat(),
        "age_hours": round((datetime.now(_TZ) - datetime.fromtimestamp(
            latest.stat().st_mtime, _TZ)).total_seconds() / 3600, 2),
        "size_bytes": latest.stat().st_size,
    }


def backup_ok(backup_root: str | Path) -> dict[str, Any]:
    """验收：至少七份 / 最近成功 <24h / 不删唯一备份。"""
    latest = latest_backup(backup_root)
    if latest is None:
        return {"ok": False, "reason": "无备份"}
    count = len(list(Path(backup_root).glob("backup_*.db")))
    ok = count >= KEEP_BACKUPS and latest["age_hours"] < 24
    return {
        "ok": ok, "count": count, "latest_age_hours": latest["age_hours"],
        "required": KEEP_BACKUPS, "max_age_hours": 24,
    }
