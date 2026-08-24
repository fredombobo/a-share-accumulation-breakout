"""全站审计服务（V2R-O2）：append-only + hash chain + 每日签名锚定。

修复项：
- `AUDIT_SIGNING_KEY` 不再硬编码；由调用方注入或从受忽略环境变量读取；
  缺失时签名拒绝（`AUDIT_SIGNING_KEY_MISSING`），日志/报告/数据库不得出现 key。
- `record_audit_event` 真正幂等：事件 ID 由业务内容（actor/action/request/
  correlation/before/after 的脱敏摘要）派生，不再包含当前时点；同请求重放
  返回既有事件，不重复写。
- 并发防分叉：SELECT head + INSERT 置于 `BEGIN IMMEDIATE` 原子事务。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

# 敏感键名标签（递归脱敏：token/密码/API key/密钥类键值 → [REDACTED]）
_SENSITIVE_TAGS = ("token", "password", "secret", "apikey", "api_key", "key")
# 完整账户号等敏感模式（持久记录禁止落盘）
_SENSITIVE_PATTERNS = (
    "account_number", "account_no", "bank_account", "card_number", "full_account",
)


class AuditError(RuntimeError):
    """审计错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _sanitize(value: Any) -> Any:
    """敏感字段递归脱敏（token/密码/API key/完整账户号 → [REDACTED]）。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            low = str(k).lower()
            if any(tag in low for tag in _SENSITIVE_TAGS) or any(pattern in low for pattern in _SENSITIVE_PATTERNS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str) and len(value) == 18 and value.isdigit():
        # 18 位数字完整账户号（非证券代码等短数字）→ 脱敏
        return "[REDACTED]"
    return value


def _require_audit_table(conn: sqlite3.Connection) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_events'"
    ).fetchone()
    if not has:
        raise AuditError("audit_events 表不存在：先运行 migrate_v2.py --apply（fail-closed）")


def _resolve_signing_key(signing_key: bytes | str | None) -> bytes:
    """签名密钥解析：调用方注入 > 环境变量 AUDIT_SIGNING_KEY；缺失 → 拒绝签名。"""
    if signing_key is not None:
        return signing_key if isinstance(signing_key, bytes) else signing_key.encode()
    env = os.environ.get("AUDIT_SIGNING_KEY")
    if env:
        return env.encode()
    raise AuditError(
        "AUDIT_SIGNING_KEY_MISSING: 未注入签名密钥（调用方参数或环境变量 "
        "AUDIT_SIGNING_KEY），拒绝签名"
    )


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
    """记录审计事件（hash chain；同业务内容幂等；BEGIN IMMEDIATE 防分叉）。"""
    _require_audit_table(conn)
    request_san = _sanitize(request)
    before_san = _sanitize(before) if before is not None else None
    after_san = _sanitize(after) if after is not None else None
    content = {
        "actor": actor, "action": action, "request": request_san,
        "correlation_id": correlation_id,
        "before": before_san, "after": after_san,
    }
    event_id = _sha256(_canonical_json(content))[:16]
    now = _now()

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT 1 FROM audit_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing:
            conn.rollback()
            return event_id
        prev = conn.execute(
            "SELECT event_hash FROM audit_events ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev[0] if prev else "GENESIS"
        payload = {**content, "prev_hash": prev_hash, "occurred_at": now}
        event_hash = _sha256(_canonical_json(payload))
        conn.execute(
            "INSERT INTO audit_events (event_id, actor, action, request_json, correlation_id,"
            " before_json, after_json, event_hash, prev_hash, occurred_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, actor, action, json.dumps(request_san, ensure_ascii=False, sort_keys=True),
             correlation_id,
             json.dumps(before_san, ensure_ascii=False, sort_keys=True) if before_san is not None else None,
             json.dumps(after_san, ensure_ascii=False, sort_keys=True) if after_san is not None else None,
             event_hash, prev_hash, payload["occurred_at"]),
        )
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise


def verify_audit_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """重算 hash chain：任何断链/内容不一致 → invalid。"""
    _require_audit_table(conn)
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
        recomputed = _sha256(_canonical_json(payload))
        if recomputed != r[1]:
            broken.append(f"{r[0]}: 内容哈希不一致（篡改）")
        prev = r[1]
    return {"valid": not broken, "events": len(rows), "broken": broken}


def sign_chain_head(
    conn: sqlite3.Connection, anchor_dir: str | Path, *, signing_key: bytes | str | None = None
) -> str:
    """每日 chain head 签名并锚定到 anchor_dir；密钥缺失 → AUDIT_SIGNING_KEY_MISSING。"""
    key = _resolve_signing_key(signing_key)
    _require_audit_table(conn)
    row = conn.execute(
        "SELECT event_hash, occurred_at FROM audit_events"
        " ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise AuditError("审计链为空，无法签名")
    head = row[0]
    anchor_dir = Path(anchor_dir)
    anchor_dir.mkdir(parents=True, exist_ok=True)
    signature = hmac.new(key, head.encode(), hashlib.sha256).hexdigest()
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


def chain_head(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """当前审计链 head（soak 证据披露用）。"""
    _require_audit_table(conn)
    row = conn.execute(
        "SELECT event_id, event_hash, prev_hash, occurred_at FROM audit_events"
        " ORDER BY occurred_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "event_id": row[0], "event_hash": row[1], "prev_hash": row[2],
        "occurred_at": row[3],
    }


def verify_chain_head(
    conn: sqlite3.Connection, anchor_path: str | Path, *, signing_key: bytes | str | None = None
) -> bool:
    """验证器：重算链 + 校验锚定签名；密钥缺失 → 拒绝验证。"""
    key = _resolve_signing_key(signing_key)
    anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    if not verify_audit_chain(conn)["valid"]:
        return False
    expected = hmac.new(key, anchor["head"].encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, anchor["signature"])
