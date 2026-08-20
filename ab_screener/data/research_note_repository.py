"""Review 台账数据访问（P7.4）：研究笔记 / 决策日志（append-only 语义）。

- 所有写操作为 INSERT（不 UPDATE/DELETE）；更新采用新行追加。
- 笔记与决策统一引用 run/signal/order/experiment IDs，禁止自由文本猜测关联。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

_ALLOWED_REF_TYPES = ("experiment", "run", "signal", "order", "candidate", "shadow",
                      "retirement", "none")
_ALLOWED_KINDS = ("idea", "hypothesis", "decision", "log", "weekly")
_REQUIRED_NOTE_FIELDS = ("title",)


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _require_table(conn: sqlite3.Connection, table: str) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not has:
        raise RuntimeError(f"v2:review 未迁移：缺少表 {table}（先运行 migrate_v2.py --apply）")


def create_note(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str = "",
    ref_type: str = "none",
    ref_id: str | None = None,
    kind: str = "idea",
    tags: list[str] | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    if ref_type not in _ALLOWED_REF_TYPES:
        raise ValueError(f"非法 ref_type: {ref_type!r}")
    if kind not in _ALLOWED_KINDS:
        raise ValueError(f"非法 kind: {kind!r}")
    if not title.strip():
        raise ValueError("title 不能为空")
    _require_table(conn, "research_notes")
    now = _now()
    note_id = uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO research_notes"
        " (note_id, ref_type, ref_id, kind, title, body, tags_json, created_by, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (note_id, ref_type, ref_id, kind, title, body,
         _json(tags or []), created_by, now, now),
    )
    return get_note(conn, note_id)


def get_note(conn: sqlite3.Connection, note_id: str) -> dict[str, Any]:
    _require_table(conn, "research_notes")
    row = conn.execute(
        "SELECT note_id, ref_type, ref_id, kind, title, body, tags_json,"
        " created_by, created_at, updated_at FROM research_notes WHERE note_id=?",
        (note_id,),
    ).fetchone()
    if row is None:
        raise KeyError(note_id)
    return _note_row(row)


def list_notes(
    conn: sqlite3.Connection,
    *,
    ref_type: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _require_table(conn, "research_notes")
    sql = ("SELECT note_id, ref_type, ref_id, kind, title, body, tags_json,"
           " created_by, created_at, updated_at FROM research_notes")
    conds: list[str] = []
    args: list[Any] = []
    if ref_type:
        conds.append("ref_type=?")
        args.append(ref_type)
    if kind:
        conds.append("kind=?")
        args.append(kind)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    return [_note_row(r) for r in conn.execute(sql, args).fetchall()]


def create_decision(
    conn: sqlite3.Connection,
    *,
    action: str,
    rationale: str,
    ref_type: str = "none",
    ref_id: str | None = None,
    risk_flags: list[str] | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    if not action.strip():
        raise ValueError("action 不能为空")
    if ref_type not in _ALLOWED_REF_TYPES:
        raise ValueError(f"非法 ref_type: {ref_type!r}")
    _require_table(conn, "review_decisions")
    decision_id = uuid.uuid4().hex[:16]
    decided_at = _now()
    conn.execute(
        "INSERT INTO review_decisions"
        " (decision_id, ref_type, ref_id, action, rationale, risk_flags_json, created_by, decided_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (decision_id, ref_type, ref_id, action, rationale,
         _json(risk_flags or []), created_by, decided_at),
    )
    return {
        "decision_id": decision_id,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "action": action,
        "rationale": rationale,
        "risk_flags": risk_flags or [],
        "created_by": created_by,
        "decided_at": decided_at,
    }


def list_decisions(
    conn: sqlite3.Connection,
    *,
    ref_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _require_table(conn, "review_decisions")
    sql = ("SELECT decision_id, ref_type, ref_id, action, rationale, risk_flags_json,"
           " created_by, decided_at FROM review_decisions")
    args: list[Any] = []
    if ref_type:
        sql += " WHERE ref_type=?"
        args.append(ref_type)
    sql += " ORDER BY decided_at DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    return [
        {
            "decision_id": r[0], "ref_type": r[1], "ref_id": r[2], "action": r[3],
            "rationale": r[4], "risk_flags": _loads(r[5]), "created_by": r[6],
            "decided_at": r[7],
        }
        for r in conn.execute(sql, args).fetchall()
    ]


def weekly_digest(conn: sqlite3.Connection, *, since: str | None = None) -> dict[str, Any]:
    """版本化周报素材：本周笔记/决策计数 + 最近样本。"""
    _require_table(conn, "research_notes")
    _require_table(conn, "review_decisions")
    if since:
        notes = list_notes(conn, limit=500)
        decisions = list_decisions(conn, limit=500)
        notes = [n for n in notes if n["created_at"] >= since]
        decisions = [d for d in decisions if d["created_at"] >= since]
    else:
        notes = list_notes(conn, limit=200)
        decisions = list_decisions(conn, limit=200)
    return {
        "since": since,
        "note_count": len(notes),
        "decision_count": len(decisions),
        "recent_notes": notes[:20],
        "recent_decisions": decisions[:20],
    }


def _note_row(row: Any) -> dict[str, Any]:
    return {
        "note_id": row[0], "ref_type": row[1], "ref_id": row[2], "kind": row[3],
        "title": row[4], "body": row[5], "tags": _loads(row[6]),
        "created_by": row[7], "created_at": row[8], "updated_at": row[9],
    }


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any) -> Any:
    import json
    try:
        return json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
