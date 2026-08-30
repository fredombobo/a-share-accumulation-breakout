"""Immutable strategy profile repository used by backtests and daily scans."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.profile import (
    StrategyProfile,
    default_profile,
    strategy_profile_from_dict,
)

_TZ = ZoneInfo("Asia/Shanghai")


class StrategyProfileRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


class StrategyProfileRepository:
    """Store custom versions without making reads perform implicit migrations."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _require_schema(conn: sqlite3.Connection) -> None:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_profiles'"
        ).fetchone()
        if exists is None:
            raise StrategyProfileRepositoryError(
                "STRATEGY_PROFILE_SCHEMA_MISSING",
                "策略参数档案表尚未迁移，不能读取或启用参数",
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["config_json"]))
            profile = strategy_profile_from_dict(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise StrategyProfileRepositoryError(
                "STRATEGY_PROFILE_CORRUPTED",
                "策略参数档案内容无法验证",
                {"profile_id": row["profile_id"], "version": row["version"]},
            ) from exc
        expected = str(row["config_hash"])
        actual = profile.config_hash()
        if expected != actual:
            raise StrategyProfileRepositoryError(
                "STRATEGY_PROFILE_HASH_MISMATCH",
                "策略参数档案哈希不一致，已拒绝使用",
                {
                    "profile_id": row["profile_id"],
                    "version": row["version"],
                    "expected": expected,
                    "actual": actual,
                },
            )
        return {
            "profile": profile,
            "config_hash": expected,
            "storage_status": str(row["status"]),
            "created_at": str(row["created_at"]),
        }

    def effective(self) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_schema(conn)
            rows = conn.execute(
                "SELECT * FROM strategy_profiles WHERE status='active' "
                "ORDER BY created_at DESC"
            ).fetchall()
        if len(rows) > 1:
            raise StrategyProfileRepositoryError(
                "MULTIPLE_ACTIVE_STRATEGY_PROFILES",
                "检测到多个启用中的参数档案，已拒绝开始扫描",
                {"count": len(rows)},
            )
        if rows:
            return self._decode(rows[0])
        profile = default_profile()
        return {
            "profile": profile,
            "config_hash": profile.config_hash(),
            "storage_status": "built_in",
            "created_at": None,
        }

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._require_schema(conn)
            rows = conn.execute(
                "SELECT * FROM strategy_profiles ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def activate(self, profile: StrategyProfile) -> dict[str, Any]:
        if profile.status != "active":
            raise StrategyProfileRepositoryError(
                "INVALID_STRATEGY_PROFILE_STATUS",
                "只有冻结为 active 的参数快照才能启用",
            )
        payload = json.dumps(
            profile.to_canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        config_hash = profile.config_hash()
        created_at = _now()
        with self._connect() as conn:
            self._require_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT config_hash FROM strategy_profiles WHERE profile_id=? AND version=?",
                (profile.profile_id, profile.version),
            ).fetchone()
            if existing is not None and str(existing[0]) != config_hash:
                conn.rollback()
                raise StrategyProfileRepositoryError(
                    "STRATEGY_PROFILE_VERSION_CONFLICT",
                    "同一参数版本已存在但内容不同，已拒绝覆盖",
                    {"profile_id": profile.profile_id, "version": profile.version},
                )
            conn.execute(
                "UPDATE strategy_profiles SET status='retired' WHERE status='active' "
                "AND NOT (profile_id=? AND version=?)",
                (profile.profile_id, profile.version),
            )
            if existing is None:
                conn.execute(
                    "INSERT INTO strategy_profiles(profile_id,version,schema_version,status,"
                    "config_json,config_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        profile.profile_id,
                        profile.version,
                        profile.schema_version,
                        "active",
                        payload,
                        config_hash,
                        created_at,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE strategy_profiles SET status='active' WHERE profile_id=? AND version=?",
                    (profile.profile_id, profile.version),
                )
            conn.commit()
        return self.effective()

    def reset_to_default(self) -> int:
        with self._connect() as conn:
            self._require_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE strategy_profiles SET status='retired' WHERE status='active'"
            )
            conn.commit()
            return int(cursor.rowcount)
