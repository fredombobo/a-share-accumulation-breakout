"""全站审计服务（P6.2）：append-only + hash chain + 每日签名锚定。"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

# 本机受保护签名密钥（测试/本机使用；生产应由 secrets 管理注入）
AUDIT_SIGNING_KEY = b"ab-local-audit-signing-key-v1"


class AuditError(RuntimeError):
    """审计错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize(value: Any) -> Any:
    """敏感字段脱敏（token/密码/key 类键值 → [REDACTED]）。"""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if any(tag in str(k).lower() for tag in ("token", "password", "secret", "key", "api"))
                else _sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def record_audit_event(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    request: dict[str, Any],
    correlation_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> str:
    """记录审计事件（hash chain；同 correlation+action+request 幂等）。"""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_events'"
    ).fetchone()
    if not has:
        raise AuditError("audit_events 表不存在：先运行 migrate_v2.py --apply（fail-closed）")
    request_san = _sanitize(request)
    prev = conn.execute(
        "SELECT event_hash FROM audit_events ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev[0] if prev else "GENESIS"
    payload = {
        "actor": actor, "action": action, "request": request_san,
        "correlation_id": correlation_id,
        "before": _sanitize(before) if before else None,
        "after": _sanitize(after) if after else None,
        "prev_hash": prev_hash, "occurred_at": _now(),
    }
    event_hash = _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    event_id = event_hash[:16]
    existing = conn.execute(
        "SELECT 1 FROM audit_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if existing:
        return event_id  # 幂等
    conn.execute(
        "INSERT INTO audit_events (event_id, actor, action, request_json, correlation_id,"
        " before_json, after_json, event_hash, prev_hash, occurred_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (event_id, actor, action, json.dumps(request_san, ensure_ascii=False, sort_keys=True),
         correlation_id,
         json.dumps(_sanitize(before), ensure_ascii=False, sort_keys=True) if before else None,
         json.dumps(_sanitize(after), ensure_ascii=False, sort_keys=True) if after else None,
         event_hash, prev_hash, payload["occurred_at"]),
    )
    conn.commit()
    return event_id


def verify_audit_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """重算 hash chain：任何断链 → invalid。"""
    rows = conn.execute(
        "SELECT event_id, event_hash, prev_hash, actor, action, request_json,"
        " correlation_id, before_json, after_json, occurred_at"
        " FROM audit_events ORDER BY occurred_at, rowid"
    ).fetchall()
    prev = "GENESIS"
    broken: list[str] = []
    for r in rows:
        if r[2] != prev:
            broken.append(f"{r[0]}: prev_hash 断链（期望 {prev}）")
        payload = {
            "actor": r[3], "action": r[4], "request": json.loads(r[5]),
            "correlation_id": r[6],
            "before": json.loads(r[7]) if r[7] else None,
            "after": json.loads(r[8]) if r[8] else None,
            "prev_hash": r[2], "occurred_at": r[9],
        }
        recomputed = _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        if recomputed != r[1]:
            broken.append(f"{r[0]}: 内容哈希不一致（篡改）")
        prev = r[1]
    return {"valid": not broken, "events": len(rows), "broken": broken}


def sign_chain_head(conn: sqlite3.Connection, anchor_dir: str | Path) -> str:
    """每日 chain head 签名并锚定到 AB_BACKUP_ROOT/audit-anchors/。"""
    row = conn.execute(
        "SELECT event_hash, occurred_at FROM audit_events"
        " ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise AuditError("审计链为空，无法签名")
    head = row[0]
    anchor_dir = Path(anchor_dir)
    anchor_dir.mkdir(parents=True, exist_ok=True)
    signature = hmac.new(AUDIT_SIGNING_KEY, head.encode(), hashlib.sha256).hexdigest()
    stamp = row[1].replace(":", "").replace("-", "")[:15]
    path = anchor_dir / f"audit-anchor-{stamp}-{head[:8]}.sig"
    path.write_text(json.dumps({"head": head, "signature": signature, "at": row[1]}),
                    encoding="utf-8")
    return str(path)


def list_audit_events(db_path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    from ab_screener.data.db import SchemaMissing, connect, table_exists

    with connect(db_path, readonly=True) as conn:
        if not table_exists(conn, "audit_events"):
            raise SchemaMissing("审计表未迁移")
        rows = conn.execute(
            "SELECT event_id, actor, action, correlation_id, event_hash, prev_hash, occurred_at"
            " FROM audit_events ORDER BY occurred_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "event_id": r[0],
            "actor": r[1],
            "action": r[2],
            "correlation_id": r[3],
            "event_hash": r[4],
            "prev_hash": r[5],
            "occurred_at": r[6],
        }
        for r in rows
    ]


def verify_chain_head(conn: sqlite3.Connection, anchor_path: str | Path) -> bool:
    """验证器：重算链 + 校验锚定签名。"""
    anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    if not verify_audit_chain(conn)["valid"]:
        return False
    expected = hmac.new(AUDIT_SIGNING_KEY, anchor["head"].encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, anchor["signature"])
