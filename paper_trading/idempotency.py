"""持久化 API 幂等执行器。"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from .db import tx
from .errors import DomainError

_T = TypeVar("_T")
_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _request_hash(operation: str, payload: Any) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def execute_idempotent(
    db_path: str | Path,
    key: str,
    operation: str,
    payload: Any,
    callback: Callable[[], _T],
) -> _T:
    """同 key/同请求回放响应；同 key/不同请求稳定拒绝 409 语义。"""
    db_path = Path(db_path)
    key = key.strip()
    if not key:
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    digest = _request_hash(operation, payload)
    with tx(db_path, immediate=True) as conn:
        row = conn.execute(
            "SELECT operation, request_hash, state, response_json "
            "FROM pt_api_idempotency WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if row:
            if row[0] != operation or row[1] != digest:
                raise DomainError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "同一 Idempotency-Key 不能用于不同请求",
                    details={"operation": operation},
                )
            if row[2] == "COMPLETED":
                return json.loads(row[3])
            raise DomainError(
                "IDEMPOTENCY_IN_PROGRESS", "相同请求正在处理中",
                retryable=True, details={"operation": operation},
            )
        conn.execute(
            "INSERT INTO pt_api_idempotency "
            "(idempotency_key, operation, request_hash, state, created_at) "
            "VALUES (?,?,?,'PROCESSING',?)",
            (key, operation, digest, _now()),
        )

    try:
        result = callback()
        response_json = json.dumps(result, ensure_ascii=False, default=str,
                                   separators=(",", ":"))
    except Exception:
        with tx(db_path, immediate=True) as conn:
            conn.execute(
                "DELETE FROM pt_api_idempotency WHERE idempotency_key=? AND state='PROCESSING'",
                (key,),
            )
        raise

    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "UPDATE pt_api_idempotency SET state='COMPLETED', status_code=200,"
            " response_json=?, completed_at=? WHERE idempotency_key=?",
            (response_json, _now(), key),
        )
    return result

