"""SQLite repository for persistent Lab runs, reports and isolated candidates."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.migrations_v2 import run_v2_migrations

_TZ = ZoneInfo("Asia/Shanghai")
_JSON_COLUMNS = {
    "request_json": "request",
    "checkpoint_json": "checkpoint",
    "result_json": "result",
    "is_json": "is_rows",
    "oos_json": "oos_rows",
    "baselines_json": "baselines",
    "promotion_json": "promotion",
}
_ACTIVE = ("pending", "running", "cancelling")
_UPDATABLE = {
    "status", "phase", "progress", "message", "checkpoint_json", "result_json",
    "is_json", "oos_json", "baselines_json", "promotion_json", "verdict",
    "candidate_eligible", "report_markdown", "report_sha256", "finished_at",
    "updated_at", "can_claim_edge", "config_hash", "cancel_requested",
    "worker_id", "heartbeat_at",
}


class ActiveResearchRunError(RuntimeError):
    """Raised when an atomic start would create a second active Lab run."""

    def __init__(self, active_run_id: str):
        self.active_run_id = active_run_id
        super().__init__(f"active research run already exists: {active_run_id}")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


class ResearchRunStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        run_v2_migrations(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for source, target in _JSON_COLUMNS.items():
            raw = result.pop(source, None)
            if raw is None or raw == "":
                result[target] = None
            else:
                try:
                    result[target] = json.loads(raw)
                except json.JSONDecodeError:
                    result[target] = None
        result["candidate_eligible"] = bool(result.get("candidate_eligible"))
        result["can_claim_edge"] = bool(result.get("can_claim_edge"))
        result["task_id"] = result.get("research_run_id")
        return result

    def create_run(
        self,
        research_run_id: str,
        *,
        strategy: str,
        research_mode: str,
        request: dict[str, Any],
        input_hash: str,
        dataset_version: str,
        code_version: str,
        cost_version: str,
        config_hash: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT research_run_id FROM research_runs "
                "WHERE status IN ('pending','running','cancelling') "
                "ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 1"
            ).fetchone()
            if active is not None:
                conn.rollback()
                raise ActiveResearchRunError(str(active[0]))
            conn.execute(
                """
                INSERT INTO research_runs(
                    research_run_id,strategy,research_mode,can_claim_edge,config_hash,created_at,
                    status,phase,progress,message,request_json,input_hash,dataset_version,
                    code_version,cost_version,started_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    research_run_id, strategy, research_mode, 0, config_hash, now,
                    "pending", "IS", 0, "已排队", _dumps(request), input_hash,
                    dataset_version, code_version, cost_version, now, now,
                ),
            )
            conn.commit()
        created = self.get(research_run_id)
        assert created is not None
        return created

    def update(self, research_run_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            current = self.get(research_run_id)
            if current is None:
                raise KeyError(research_run_id)
            return current
        values: dict[str, Any] = {}
        json_aliases = {
            "checkpoint": "checkpoint_json", "result": "result_json",
            "is_rows": "is_json", "oos_rows": "oos_json",
            "baselines": "baselines_json", "promotion": "promotion_json",
        }
        for key, value in fields.items():
            column = json_aliases.get(key, key)
            if column not in _UPDATABLE:
                raise ValueError(f"unsupported research_runs field: {key}")
            values[column] = _dumps(value) if column.endswith("_json") and value is not None else value
        if "candidate_eligible" in values:
            values["candidate_eligible"] = int(bool(values["candidate_eligible"]))
        if "can_claim_edge" in values:
            values["can_claim_edge"] = int(bool(values["can_claim_edge"]))
        if "cancel_requested" in values:
            values["cancel_requested"] = int(bool(values["cancel_requested"]))
        if "report_markdown" in values and values["report_markdown"] is not None:
            values["report_sha256"] = hashlib.sha256(
                str(values["report_markdown"]).encode("utf-8")
            ).hexdigest()
        if values.get("status") in ("done", "error", "cancelled") and "finished_at" not in values:
            values["finished_at"] = _now()
        values["updated_at"] = _now()
        assignments = ",".join(f"{name}=?" for name in values)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"UPDATE research_runs SET {assignments} WHERE research_run_id=?",
                (*values.values(), research_run_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise KeyError(research_run_id)
            conn.commit()
        updated = self.get(research_run_id)
        assert updated is not None
        return updated

    def request_cancel(self, research_run_id: str) -> dict[str, Any]:
        """Persist an idempotent cancellation request before workers observe it."""
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM research_runs WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError(research_run_id)
            if str(row[0]) in _ACTIVE:
                conn.execute(
                    "UPDATE research_runs SET status='cancelling', cancel_requested=1, "
                    "message='取消中…正在停止工作进程', updated_at=? "
                    "WHERE research_run_id=?",
                    (now, research_run_id),
                )
            conn.commit()
        current = self.get(research_run_id)
        assert current is not None
        return current

    def is_cancel_requested(self, research_run_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status,cancel_requested FROM research_runs WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
        if row is None:
            return True
        return bool(row[1]) or str(row[0]) in ("cancelling", "cancelled", "interrupted")

    def resume_run(self, research_run_id: str) -> bool:
        """Atomically claim one interrupted run for a single replacement worker."""
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM research_runs WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError(research_run_id)
            if str(row[0]) != "interrupted":
                conn.rollback()
                return False
            other = conn.execute(
                "SELECT research_run_id FROM research_runs "
                "WHERE research_run_id<>? AND status IN ('pending','running','cancelling') "
                "LIMIT 1",
                (research_run_id,),
            ).fetchone()
            if other is not None:
                conn.rollback()
                raise ActiveResearchRunError(str(other[0]))
            conn.execute(
                "UPDATE research_runs SET status='pending', cancel_requested=0, "
                "message='从持久化检查点恢复', finished_at=NULL, updated_at=? "
                "WHERE research_run_id=?",
                (now, research_run_id),
            )
            conn.commit()
            return True

    def get(self, research_run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE research_run_id=?", (research_run_id,)
            ).fetchone()
        return self._decode(row)

    def latest_active(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in _ACTIVE)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM research_runs WHERE status IN ({placeholders}) "
                "ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 1",
                _ACTIVE,
            ).fetchone()
        return self._decode(row)

    def latest(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 1"
            ).fetchone()
        return self._decode(row)

    def latest_for_mode(self, research_mode: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE research_mode=? "
                "ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 1",
                (research_mode,),
            ).fetchone()
        return self._decode(row)

    def completed_by_input_hash(self, input_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE input_hash=? AND status='done' "
                "ORDER BY finished_at DESC LIMIT 1",
                (input_hash,),
            ).fetchone()
        return self._decode(row)

    def resumable_by_input_hash(self, input_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE input_hash=? AND status='interrupted' "
                "ORDER BY updated_at DESC LIMIT 1",
                (input_hash,),
            ).fetchone()
        return self._decode(row)

    def mark_orphaned_interrupted(self) -> int:
        """Close lifecycle states left behind by a previous web process."""
        now = _now()
        placeholders = ",".join("?" for _ in _ACTIVE)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"UPDATE research_runs SET status='interrupted', "
                "message='服务已重启；再次提交相同配置可从检查点继续', updated_at=? "
                f"WHERE status IN ({placeholders})",
                (now, *_ACTIVE),
            )
            conn.commit()
            return int(cursor.rowcount)

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_runs WHERE report_markdown IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [decoded for row in rows if (decoded := self._decode(row)) is not None]

    def add_candidate(
        self,
        research_run_id: str,
        *,
        strategy: str,
        param_id: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"lab:{research_run_id}:{param_id}"
        ).hex[:16]
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO research_candidates "
                "(candidate_id,research_run_id,param_id,strategy,status,params_json,metrics_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (candidate_id, research_run_id, param_id, strategy, "isolated", _dumps(params), _dumps(metrics), now),
            )
            row = conn.execute(
                "SELECT * FROM research_candidates WHERE research_run_id=? AND param_id=?",
                (research_run_id, param_id),
            ).fetchone()
            conn.commit()
        assert row is not None
        result = dict(row)
        result["params"] = json.loads(result.pop("params_json"))
        result["metrics"] = json.loads(result.pop("metrics_json"))
        return result
