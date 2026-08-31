"""席位主数据 / 别名 / 身份假设仓储（append-only，按事件日 as-of 读取）。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from ab_screener.domain.data_point import canonical_json, content_hash_for
from ab_screener.domain.lhb_contracts import parse_trade_date, require_available_at
from ab_screener.domain.seat_identity import (
    SeatHypothesis,
    detect_name_conflict,
    hypothesis_from_raw,
)


def _next_revision_if_changed(
    conn: sqlite3.Connection,
    *,
    table: str,
    where_sql: str,
    params: Sequence[Any],
    content_hash: str,
) -> int | None:
    row = conn.execute(
        f"SELECT revision, content_hash FROM {table} WHERE {where_sql} "
        "ORDER BY revision DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    if row is not None and row[1] == content_hash:
        return None
    return 1 if row is None else int(row[0]) + 1


def save_hypothesis(
    conn: sqlite3.Connection,
    hyp: SeatHypothesis,
    *,
    available_at: str,
    source: str,
    confidence: float = 0.5,
) -> str:
    """内容不变幂等；内容变化时 append revision，永不原地覆盖。"""
    available = require_available_at(available_at)
    overlap_conflict = hyp.conflict or conn.execute(
        "SELECT 1 FROM seat_alias WHERE alias_raw=? AND seat_id<>?"
        " AND valid_from<COALESCE(?,'99999999')"
        " AND COALESCE(valid_to,'99999999')>? LIMIT 1",
        (hyp.seat_raw, hyp.seat_id, hyp.valid_to, hyp.valid_from),
    ).fetchone() is not None
    payload = {
        "display_name": hyp.display_name,
        "canonical_name": hyp.canonical_name,
        "seat_raw": hyp.seat_raw,
    }
    master_hash = content_hash_for(
        {
            "canonical_name": hyp.canonical_name,
            "official_tag": hyp.official_tag,
            "valid_from": hyp.valid_from,
            "valid_to": hyp.valid_to,
        }
    )
    master_revision = _next_revision_if_changed(
        conn,
        table="seat_master",
        where_sql="seat_id=?",
        params=(hyp.seat_id,),
        content_hash=master_hash,
    )
    if master_revision is not None:
        conn.execute(
            "INSERT INTO seat_master (seat_id, revision, canonical_name, official_tag, broker_name,"
            " branch_city, valid_from, valid_to, source, available_at, ingested_at, content_hash,"
            " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                hyp.seat_id,
                master_revision,
                hyp.canonical_name,
                hyp.official_tag,
                None,
                None,
                hyp.valid_from,
                hyp.valid_to,
                source,
                available,
                available,
                master_hash,
                canonical_json(payload),
            ),
        )
    alias_hash = content_hash_for(
        {
            "alias": hyp.seat_raw,
            "seat_id": hyp.seat_id,
            "valid_from": hyp.valid_from,
            "valid_to": hyp.valid_to,
        }
    )
    alias_revision = _next_revision_if_changed(
        conn,
        table="seat_alias",
        where_sql="alias_raw=? AND seat_id=?",
        params=(hyp.seat_raw, hyp.seat_id),
        content_hash=alias_hash,
    )
    if alias_revision is not None:
        conn.execute(
            "INSERT INTO seat_alias (alias_raw, seat_id, revision, valid_from, valid_to, source,"
            " available_at, ingested_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                hyp.seat_raw,
                hyp.seat_id,
                alias_revision,
                hyp.valid_from,
                hyp.valid_to,
                source,
                available,
                available,
                alias_hash,
            ),
        )
    actor_hash = content_hash_for(
        {
            "actor_type": hyp.actor_type,
            "display_name": hyp.display_name,
            "valid_from": hyp.valid_from,
            "valid_to": hyp.valid_to,
        }
    )
    actor_revision = _next_revision_if_changed(
        conn,
        table="actor_master",
        where_sql="actor_id=?",
        params=(hyp.actor_id,),
        content_hash=actor_hash,
    )
    if actor_revision is not None:
        conn.execute(
            "INSERT INTO actor_master (actor_id, revision, actor_type, display_name, valid_from,"
            " valid_to, source, available_at, ingested_at, content_hash, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                hyp.actor_id,
                actor_revision,
                hyp.actor_type,
                hyp.display_name,
                hyp.valid_from,
                hyp.valid_to,
                source,
                available,
                available,
                actor_hash,
                canonical_json({"evidence_source": hyp.evidence_source}),
            ),
        )
    hypothesis_hash = content_hash_for(
        {
            "valid_to": hyp.valid_to,
            "confidence": confidence,
            "evidence_grade": hyp.evidence_grade,
            "evidence_source": hyp.evidence_source,
            "conflict": overlap_conflict,
            "display_name": hyp.display_name,
        }
    )
    hypothesis_revision = _next_revision_if_changed(
        conn,
        table="seat_actor_hypothesis",
        where_sql="seat_id=? AND actor_id=? AND valid_from=?",
        params=(hyp.seat_id, hyp.actor_id, hyp.valid_from),
        content_hash=hypothesis_hash,
    )
    if hypothesis_revision is not None:
        conn.execute(
            "INSERT INTO seat_actor_hypothesis (seat_id, actor_id, revision, valid_from, valid_to,"
            " confidence, evidence_grade, evidence_source, conflict_status, hypothesis_note, source,"
            " available_at, ingested_at, content_hash, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                hyp.seat_id,
                hyp.actor_id,
                hypothesis_revision,
                hyp.valid_from,
                hyp.valid_to,
                confidence,
                hyp.evidence_grade,
                hyp.evidence_source,
                "OPEN" if overlap_conflict else "NONE",
                hyp.display_name,
                source,
                available,
                available,
                hypothesis_hash,
                canonical_json(payload),
            ),
        )
    conn.commit()
    return hyp.seat_id


def lookup_candidates_as_of(
    conn: sqlite3.Connection,
    *,
    alias_raw: str,
    event_date: str,
    knowledge_as_of: str | None = None,
) -> list[dict[str, Any]]:
    """同时按事件有效期和当时可获得时间读取，返回一席位多人物候选。"""
    day = parse_trade_date(event_date)
    known = require_available_at(knowledge_as_of or f"{day}T23:59:59+08:00")
    rows = conn.execute(
        "WITH ar AS ("
        " SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.alias_raw,a.seat_id"
        " ORDER BY a.revision DESC) AS rn FROM seat_alias a"
        " WHERE a.alias_raw=? AND a.valid_from<=?"
        " AND (a.valid_to IS NULL OR a.valid_to>?) AND a.available_at<=?"
        "), sm AS ("
        " SELECT m.*, ROW_NUMBER() OVER (PARTITION BY m.seat_id ORDER BY m.revision DESC) AS rn"
        " FROM seat_master m WHERE m.valid_from<=?"
        " AND (m.valid_to IS NULL OR m.valid_to>?) AND m.available_at<=?"
        "), hp AS ("
        " SELECT h.*, ROW_NUMBER() OVER (PARTITION BY h.seat_id,h.actor_id,h.valid_from"
        " ORDER BY h.revision DESC) AS rn FROM seat_actor_hypothesis h"
        " WHERE h.valid_from<=? AND (h.valid_to IS NULL OR h.valid_to>?) AND h.available_at<=?"
        "), am AS ("
        " SELECT x.*, ROW_NUMBER() OVER (PARTITION BY x.actor_id ORDER BY x.revision DESC) AS rn"
        " FROM actor_master x WHERE x.valid_from<=?"
        " AND (x.valid_to IS NULL OR x.valid_to>?) AND x.available_at<=?"
        ")"
        " SELECT a.seat_id,a.valid_from,a.valid_to,m.official_tag,m.canonical_name,"
        " CASE WHEN x.actor_id IS NOT NULL THEN h.evidence_grade END,"
        " CASE WHEN x.actor_id IS NOT NULL THEN h.evidence_source END,"
        " CASE WHEN x.actor_id IS NOT NULL THEN h.conflict_status END,"
        " CASE WHEN x.actor_id IS NOT NULL THEN h.confidence END,"
        " CASE WHEN x.actor_id IS NOT NULL THEN x.display_name END,"
        " x.actor_id"
        " FROM ar a JOIN sm m ON m.seat_id=a.seat_id AND m.rn=1"
        " LEFT JOIN hp h ON h.seat_id=a.seat_id AND h.rn=1"
        " LEFT JOIN am x ON x.actor_id=h.actor_id AND x.rn=1"
        " WHERE a.rn=1"
        " ORDER BY COALESCE(h.confidence,-1) DESC,a.seat_id,COALESCE(x.actor_id,'')",
        (
            alias_raw,
            day,
            day,
            known,
            day,
            day,
            known,
            day,
            day,
            known,
            day,
            day,
            known,
        ),
    ).fetchall()
    return [
        {
            "seat_id": row[0],
            "valid_from": row[1],
            "valid_to": row[2],
            "official_tag": row[3],
            "canonical_name": row[4],
            "evidence_grade": row[5],
            "evidence_source": row[6],
            "conflict_status": row[7],
            "confidence": row[8],
            "display_name": row[9],
            "actor_id": row[10],
            "knowledge_as_of": known,
        }
        for row in rows
    ]


def lookup_as_of(
    conn: sqlite3.Connection,
    *,
    alias_raw: str,
    event_date: str,
    knowledge_as_of: str | None = None,
) -> dict[str, Any] | None:
    candidates = lookup_candidates_as_of(
        conn,
        alias_raw=alias_raw,
        event_date=event_date,
        knowledge_as_of=knowledge_as_of,
    )
    return candidates[0] if candidates else None


def queue_if_conflict(left: SeatHypothesis, right: SeatHypothesis) -> list[SeatHypothesis]:
    """冲突不自动合并，进入复核列表。"""
    if detect_name_conflict(left, right):
        return [replace(left, conflict=True), replace(right, conflict=True)]
    return []


def load_alias_rows(rows: Iterable[dict[str, str]], *, event_date: str) -> list[SeatHypothesis]:
    out: list[SeatHypothesis] = []
    for row in rows:
        out.append(
            hypothesis_from_raw(
                row["alias_raw"],
                event_date=event_date,
                valid_from=row.get("valid_from") or "19900101",
                valid_to=row.get("valid_to") or None,
            )
        )
    return out
