"""公司行为仓库：append-only 事件账本 + 冲正 + as-of 复权因子读取。

契约（implementation P1.3 / V2R-D）：
- `corporate_actions` 账本只追加：内容（含 reversal_of）仅在 INSERT 写入，
  禁止 UPDATE/DELETE（触发器兜底）。状态是独立投影 `corporate_action_status`。
- 更正一律追加 REVERSAL 事件 + 投影标记原事件 REVERSED（追加冲正，不改账本行）。
- 重复 (ts_code, ex_date, kind, checksum) 幂等跳过（不重复入账）。
- 每条记录携带 PIT 五元组：effective_at / available_at / ingested_at / source / revision。
  revision 对同一业务键（ts_code, ex_date, kind）单调递增，幂等入账沿用既有 revision。
- 未处理（PENDING）事件阻断估值与日结——由 service/settlement 判定。
- as-of 复权因子复用 P1.1 的 adj_factor_history PIT 表。
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json, normalize_ts

_TZ = ZoneInfo("Asia/Shanghai")
VALID_KINDS = ("SPLIT", "DIVIDEND", "RIGHTS", "REVERSAL")


class CorporateActionError(RuntimeError):
    """公司行为领域错误（阻断信号）。"""


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def _set_status(conn: sqlite3.Connection, action_id: int, status: str) -> None:
    conn.execute(
        "INSERT INTO corporate_action_status (corporate_action_id, status, updated_at)"
        " VALUES (?,?,?)"
        " ON CONFLICT(corporate_action_id) DO UPDATE SET status=excluded.status,"
        " updated_at=excluded.updated_at",
        (action_id, status, datetime.now(_TZ).isoformat(timespec="seconds")),
    )


def _next_revision(conn: sqlite3.Connection, ts_code: str, ex_date: str, kind: str) -> int:
    """同一业务键（ts_code, ex_date, kind）的下一 revision（现有最大 + 1）。"""
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM corporate_actions"
        " WHERE ts_code=? AND ex_date=? AND kind=?",
        (ts_code, ex_date, kind),
    ).fetchone()
    return int(row[0] or 0) + 1


def add_action(
    conn: sqlite3.Connection,
    *,
    ts_code: str,
    ex_date: str,
    kind: str,
    payload: dict[str, Any],
    source: str,
    available_at: Any = None,
    effective_at: Any = None,
    ingested_at: Any = None,
) -> int:
    """追加公司行为事件（账本 + 状态投影）；重复键幂等返回既有 action_id。

    记录同时携带 PIT 元数据：effective_at（默认 ex_date）、ingested_at（默认 now）。
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"非法公司行为 kind: {kind}")
    if not ts_code or not ex_date:
        raise ValueError("公司行为缺 ts_code/ex_date")
    checksum = _checksum(payload)
    available = normalize_ts(available_at or datetime.now(_TZ))
    effective = normalize_ts(effective_at or ex_date)
    ingested = normalize_ts(ingested_at or datetime.now(_TZ))
    existing = conn.execute(
        "SELECT corporate_action_id FROM corporate_actions"
        " WHERE ts_code=? AND ex_date=? AND kind=? AND checksum=?",
        (ts_code, ex_date, kind, checksum),
    ).fetchone()
    if existing:
        return int(existing[0])
    revision = _next_revision(conn, ts_code, ex_date, kind)
    cur = conn.execute(
        "INSERT INTO corporate_actions (ts_code, ex_date, kind, payload_json, source,"
        " available_at, checksum, effective_at, ingested_at, revision)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts_code, ex_date, kind, canonical_json(payload), source, available, checksum,
         effective, ingested, revision),
    )
    action_id = int(cur.lastrowid or 0)
    if action_id <= 0:
        raise CorporateActionError("公司行为入账失败（未返回 action_id）")
    _set_status(conn, action_id, "PENDING")
    conn.commit()
    return action_id


def pending_actions(
    conn: sqlite3.Connection,
    ts_codes: Iterable[str] | None = None,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """未处理（PENDING）事件；可按时点与标的过滤。"""
    sql = (
        "SELECT a.corporate_action_id, a.ts_code, a.ex_date, a.kind, a.payload_json,"
        " a.source, a.available_at, a.reversal_of"
        " FROM corporate_actions a JOIN corporate_action_status s"
        " ON a.corporate_action_id = s.corporate_action_id"
        " WHERE s.status='PENDING'"
    )
    params: list[Any] = []
    if ts_codes is not None:
        codes = list(ts_codes)
        placeholders = ",".join("?" * len(codes))
        sql += f" AND a.ts_code IN ({placeholders})"
        params.extend(codes)
    if as_of is not None:
        sql += " AND a.ex_date <= ?"
        params.append(as_of)
    rows = conn.execute(sql + " ORDER BY a.ex_date, a.corporate_action_id", params).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "corporate_action_id": r[0],
                "ts_code": r[1],
                "ex_date": r[2],
                "kind": r[3],
                "payload": json_loads(r[4]),
                "source": r[5],
                "available_at": r[6],
                "reversal_of": r[7],
            }
        )
    return out


def has_unprocessed_for(conn: sqlite3.Connection, ts_codes: Iterable[str], as_of: str) -> bool:
    """持仓/订单标的在 as_of 前存在未处理事件 → True（阻断估值/日结）。"""
    return bool(pending_actions(conn, ts_codes=list(ts_codes), as_of=as_of))


def apply_reversal(
    conn: sqlite3.Connection,
    *,
    original_id: int,
    payload: dict[str, Any],
    source: str,
    available_at: Any = None,
) -> int:
    """追加冲正事件 + 投影标记原事件 REVERSED（只追加，不改账本行内容）。"""
    row = conn.execute(
        "SELECT ts_code, ex_date, kind FROM corporate_actions WHERE corporate_action_id=?",
        (original_id,),
    ).fetchone()
    if row is None:
        raise CorporateActionError(f"原公司行为不存在: {original_id}")
    status_row = conn.execute(
        "SELECT status FROM corporate_action_status WHERE corporate_action_id=?",
        (original_id,),
    ).fetchone()
    if status_row and status_row[0] == "REVERSED":
        raise CorporateActionError(f"原事件已冲正: {original_id}（重复冲正拒绝）")
    checksum = _checksum(payload)
    available = normalize_ts(available_at or datetime.now(_TZ))
    ingested = normalize_ts(datetime.now(_TZ))
    revision = _next_revision(conn, row[0], row[1], "REVERSAL")
    cur = conn.execute(
        "INSERT INTO corporate_actions (ts_code, ex_date, kind, payload_json, source,"
        " available_at, checksum, reversal_of, effective_at, ingested_at, revision)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (row[0], row[1], "REVERSAL", canonical_json(payload), source, available, checksum,
         original_id, row[1], ingested, revision),
    )
    reversal_id = int(cur.lastrowid or 0)
    if reversal_id <= 0:
        raise CorporateActionError("冲正入账失败（未返回 action_id）")
    _set_status(conn, original_id, "REVERSED")
    _set_status(conn, reversal_id, "PENDING")
    conn.commit()
    return reversal_id


def mark_applied(conn: sqlite3.Connection, action_id: int) -> None:
    _set_status(conn, action_id, "APPLIED")
    conn.commit()


def adj_factor_asof(conn: sqlite3.Connection, ts_code: str, as_of: str) -> float:
    """as_of 时刻的复权因子（读 adj_factor_history PIT，缺记录 fail-closed）。"""
    row = conn.execute(
        "SELECT payload_json FROM adj_factor_history"
        " WHERE ts_code=? AND available_at <= ? ORDER BY revision DESC LIMIT 1",
        (ts_code, normalize_ts(as_of)),
    ).fetchone()
    if row is None:
        raise CorporateActionError(
            f"adj_factor_history 无 {ts_code} 在 {as_of} 的复权因子（fail-closed）"
        )
    payload = json_loads(row[0])
    factor = payload.get("adj_factor")
    if factor is None:
        raise CorporateActionError(f"{ts_code} 复权因子载荷缺 adj_factor 字段")
    return float(factor)


def json_loads(text: str) -> dict[str, Any]:
    import json

    return json.loads(text)


class CorporateActionRepository:
    """公司行为 PIT 仓库：append 幂等入账 + decision_at 时刻 as-of 读取。

    V2R-D：每条记录必须携带 effective_at / available_at / ingested_at / source / revision。
    - `append`：同一业务键（ts_code, ex_date, kind）同一载荷幂等；不同载荷生成新 revision。
    - `list_asof`：返回 decision_at 时刻可见（available_at <= decision_at）的记录，
      同一业务键只返回该时刻 revision 最大的一条（可用性门控）。
    - 表未迁移（v2:corporate_action_pit 缺失列）→ 显式抛 CorporateActionError（fail-closed）。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        # 自包含：确保账本 + PIT 列迁移就绪（幂等；测试可直接 append）
        from ab_screener.data.migration_registry import apply_pending

        with sqlite3.connect(str(db_path), timeout=60) as conn:
            apply_pending(conn)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=60)

    def _require_pit_columns(self, conn: sqlite3.Connection) -> None:
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(corporate_actions)").fetchall()}
        except sqlite3.OperationalError as exc:
            raise CorporateActionError(
                "corporate_actions 表不存在：先运行迁移（fail-closed）"
            ) from exc
        missing = {c for c in ("effective_at", "ingested_at", "revision") if c not in cols}
        if missing:
            raise CorporateActionError(
                f"corporate_actions 缺 PIT 列 {sorted(missing)}：先运行迁移 v2:corporate_action_pit"
            )

    def append(self, action: dict[str, Any]) -> int:
        """幂等入账一条公司行为记录（与 add_action 同一账本与投影）。

        action 支持字段：ts_code, ex_date, kind, payload, source, available_at, effective_at。
        """
        ts_code = str(action["ts_code"])
        ex_date = str(action["ex_date"])
        kind = str(action["kind"])
        payload = dict(action.get("payload") or {})
        source = str(action.get("source") or "tushare")
        with self._conn() as conn:
            self._require_pit_columns(conn)
            return add_action(
                conn,
                ts_code=ts_code,
                ex_date=ex_date,
                kind=kind,
                payload=payload,
                source=source,
                available_at=action.get("available_at"),
                effective_at=action.get("effective_at"),
            )

    def list_asof(self, ts_code: str, decision_at: Any) -> list[dict[str, Any]]:
        """decision_at 时刻可见的公司行为记录（每业务键取该时刻最大 revision）。"""
        decision = normalize_ts(decision_at)
        with self._conn() as conn:
            self._require_pit_columns(conn)
            rows = conn.execute(
                "SELECT corporate_action_id, ts_code, ex_date, kind, payload_json,"
                " source, available_at, effective_at, ingested_at, revision, checksum,"
                " reversal_of"
                " FROM corporate_actions a"
                " WHERE a.ts_code=? AND a.available_at <= ?"
                " AND a.revision = (SELECT MAX(b.revision) FROM corporate_actions b"
                "   WHERE b.ts_code=a.ts_code AND b.ex_date=a.ex_date AND b.kind=a.kind"
                "     AND b.available_at <= ?)"
                " ORDER BY a.ex_date, a.corporate_action_id",
                (ts_code, decision, decision),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "corporate_action_id": r[0],
                    "ts_code": r[1],
                    "ex_date": r[2],
                    "kind": r[3],
                    "payload": json_loads(r[4]),
                    "source": r[5],
                    "available_at": r[6],
                    "effective_at": r[7],
                    "ingested_at": r[8],
                    "revision": r[9],
                    "checksum": r[10],
                    "reversal_of": r[11],
                }
            )
        return out

    def list_revisions(self, ts_code: str, ex_date: str, kind: str) -> list[dict[str, Any]]:
        """同一业务键的全部修订（升序），供 revision 语义审计。"""
        with self._conn() as conn:
            self._require_pit_columns(conn)
            rows = conn.execute(
                "SELECT corporate_action_id, ts_code, ex_date, kind, payload_json,"
                " source, available_at, effective_at, ingested_at, revision, checksum,"
                " reversal_of"
                " FROM corporate_actions"
                " WHERE ts_code=? AND ex_date=? AND kind=?"
                " ORDER BY revision ASC, corporate_action_id ASC",
                (ts_code, ex_date, kind),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "corporate_action_id": r[0],
                    "ts_code": r[1],
                    "ex_date": r[2],
                    "kind": r[3],
                    "payload": json_loads(r[4]),
                    "source": r[5],
                    "available_at": r[6],
                    "effective_at": r[7],
                    "ingested_at": r[8],
                    "revision": r[9],
                    "checksum": r[10],
                    "reversal_of": r[11],
                }
            )
        return out
