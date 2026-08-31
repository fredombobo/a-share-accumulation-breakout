"""龙虎榜仓储：画像快照 / 信号观察 / 事件查询（append-only）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json, content_hash_for
from ab_screener.domain.lhb_contracts import parse_trade_date, require_available_at

_TZ = ZoneInfo("Asia/Shanghai")
AMOUNT_UNIT = "yuan"
POLICY_VERSION_DEFAULT = "lhb-signal-v1"


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def envelope(
    *,
    source_status: str,
    items: list[Any] | None = None,
    as_of: str,
    available_at: str | None = None,
    error_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": as_of,
        "available_at": available_at or as_of,
        "source_status": source_status,
        "source": "lhb",
        "amount_unit": AMOUNT_UNIT,
        "policy_version": POLICY_VERSION_DEFAULT,
        "model_version": "lhb-features-v1",
        "research_only": True,
        "items": items or [],
        "count": len(items or []),
        "error_reason": error_reason,
    }
    if extra:
        payload.update(extra)
    return payload


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def connect_path(db_path: str | Path, *, readonly: bool = True) -> sqlite3.Connection:
    path = Path(db_path)
    if readonly:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_latest_raw_day(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """重建某日最近一次完整抓取批次，保留同股多上榜原因。"""
    parse_trade_date(trade_date)
    keys: tuple[str, ...]
    if dataset == "top_list":
        table = "top_list_history"
        keys = ("ts_code", "trade_date")
    elif dataset == "top_inst":
        table = "top_inst_history"
        keys = ("ts_code", "trade_date", "exalter", "reason", "side")
    else:
        raise ValueError(f"不支持的龙虎榜日分区: {dataset}")
    if not _table_exists(conn, table):
        return []
    latest = conn.execute(
        f"SELECT MAX(available_at) FROM {table} WHERE trade_date=?", (trade_date,)
    ).fetchone()
    if latest is None or latest[0] is None:
        return []
    columns = ",".join(keys)
    rows = conn.execute(
        f"SELECT {columns},payload_json FROM {table}"
        " WHERE trade_date=? AND available_at=? ORDER BY revision",
        (trade_date, latest[0]),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row[len(keys)]) if row[len(keys)] else {}
        out.append({**dict(zip(keys, row[: len(keys)], strict=True)), **payload})
    return out


def persist_normalized_day(
    conn: sqlite3.Connection,
    normalized: Any,
    *,
    available_at: str,
    source: str = "tushare",
) -> dict[str, int]:
    """把 ``transform_day`` 产物幂等写入标准事实，并同步基础席位主数据。"""
    from ab_screener.data.seat_repository import lookup_as_of, save_hypothesis
    from ab_screener.domain.seat_identity import hypothesis_from_raw

    available = require_available_at(available_at)
    counts = {"events": 0, "trades": 0, "ranks": 0, "seats": 0}
    event_dates = {event.key.event_id: event.key.disclose_date for event in normalized.events}
    for event in normalized.events:
        payload = dict(event.payload)
        payload["flow_fingerprint"] = event.flow_fingerprint
        body = {
            "exchange": event.key.exchange,
            "ts_code": event.key.ts_code,
            "window_code": event.key.window_code,
            "reason_code": event.key.reason_code,
            "reason_raw": event.reason_raw,
            "disclose_date": event.key.disclose_date,
            "period_start": event.period_start,
            "period_end": event.period_end,
            "source_status": event.source_status,
            "payload": payload,
        }
        digest = content_hash_for(body)
        revision = _next_revision(
            conn, "lhb_event", "event_id=?", (event.key.event_id,), digest
        )
        if revision is None:
            continue
        conn.execute(
            "INSERT INTO lhb_event (event_id,revision,exchange,ts_code,window_code,reason_code,"
            " reason_raw,reason_catalog_version,disclose_date,period_start,period_end,"
            " flow_fingerprint,source,source_status,available_at,ingested_at,content_hash,payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.key.event_id,
                revision,
                event.key.exchange,
                event.key.ts_code,
                event.key.window_code,
                event.key.reason_code,
                event.reason_raw,
                str(payload.get("reason_catalog_version") or "v1"),
                event.key.disclose_date,
                event.period_start,
                event.period_end,
                event.flow_fingerprint,
                source,
                event.source_status,
                available,
                _now(),
                digest,
                canonical_json(payload),
            ),
        )
        counts["events"] += 1

    seat_ids: dict[tuple[str, str], str] = {}
    for trade in normalized.trades:
        day = event_dates[trade.event_id]
        lookup = lookup_as_of(
            conn,
            alias_raw=trade.seat_raw,
            event_date=day,
            knowledge_as_of=available,
        )
        if lookup is None:
            hyp = hypothesis_from_raw(trade.seat_raw, event_date=day)
            save_hypothesis(conn, hyp, available_at=available, source="lhb-auto-tag")
            seat_id = hyp.seat_id
            counts["seats"] += 1
        else:
            seat_id = str(lookup["seat_id"])
        seat_ids[(trade.event_id, trade.seat_raw)] = seat_id
        body = {
            "seat_id": seat_id,
            "buy_amount_fen": trade.buy_fen,
            "sell_amount_fen": trade.sell_fen,
            "net_amount_fen": trade.net_fen,
        }
        digest = content_hash_for(body)
        revision = _next_revision(
            conn,
            "lhb_seat_trade",
            "event_id=? AND seat_raw=?",
            (trade.event_id, trade.seat_raw),
            digest,
        )
        if revision is None:
            continue
        conn.execute(
            "INSERT INTO lhb_seat_trade (event_id,seat_raw,seat_id,revision,buy_amount_fen,"
            " sell_amount_fen,net_amount_fen,source,available_at,ingested_at,content_hash,payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade.event_id,
                trade.seat_raw,
                seat_id,
                revision,
                trade.buy_fen,
                trade.sell_fen,
                trade.net_fen,
                source,
                available,
                _now(),
                digest,
                canonical_json(body),
            ),
        )
        counts["trades"] += 1

    for rank in normalized.ranks:
        seat_id = seat_ids.get((rank.event_id, rank.seat_raw))
        body = {"seat_id": seat_id, "rank_no": rank.rank_no}
        digest = content_hash_for(body)
        revision = _next_revision(
            conn,
            "lhb_seat_rank",
            "event_id=? AND seat_raw=? AND side=?",
            (rank.event_id, rank.seat_raw, rank.side),
            digest,
        )
        if revision is None:
            continue
        conn.execute(
            "INSERT INTO lhb_seat_rank (event_id,seat_raw,seat_id,side,rank_no,revision,source,"
            " available_at,ingested_at,content_hash,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rank.event_id,
                rank.seat_raw,
                seat_id,
                rank.side,
                rank.rank_no,
                revision,
                source,
                available,
                _now(),
                digest,
                canonical_json(body),
            ),
        )
        counts["ranks"] += 1
    conn.commit()
    return counts


def partition_status(conn: sqlite3.Connection, trade_date: str) -> str:
    parse_trade_date(trade_date)
    if not _table_exists(conn, "lhb_ingest_manifests"):
        return "FETCH_FAILED"
    rows = conn.execute(
        "WITH ranked AS (SELECT dataset,source_status,revision,"
        " ROW_NUMBER() OVER (PARTITION BY dataset,source ORDER BY revision DESC) rn"
        " FROM lhb_ingest_manifests WHERE partition_key=?)"
        " SELECT source_status FROM ranked WHERE rn=1",
        (trade_date,),
    ).fetchall()
    if not rows:
        return "NOT_PUBLISHED"
    statuses = {str(row[0]) for row in rows}
    if "FETCH_FAILED" in statuses:
        return "FETCH_FAILED"
    if "NOT_PUBLISHED" in statuses:
        return "NOT_PUBLISHED"
    if "DEGRADED" in statuses or ("COMPLETE" in statuses and "VALID_EMPTY" in statuses):
        return "DEGRADED"
    if statuses == {"VALID_EMPTY"}:
        return "VALID_EMPTY"
    return "COMPLETE"


def _next_revision(
    conn: sqlite3.Connection,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    content_hash: str,
) -> int | None:
    row = conn.execute(
        f"SELECT revision, content_hash FROM {table} WHERE {where_sql}"
        " ORDER BY revision DESC LIMIT 1",
        params,
    ).fetchone()
    if row is not None and row[1] == content_hash:
        return None
    return 1 if row is None else int(row[0]) + 1


def save_profile_snapshot(
    conn: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    as_of: str,
    source: str = "lhb-profiles",
) -> str:
    require_available_at(as_of)
    payload = canonical_json(profile)
    digest = content_hash_for(profile)
    snapshot_id = f"{profile['subject_type']}:{profile['subject_id']}:{profile['window_days']}"
    revision = _next_revision(
        conn, "lhb_feature_snapshot", "snapshot_id=?", (snapshot_id,), digest
    )
    if revision is None:
        return snapshot_id
    now = _now()
    conn.execute(
        "INSERT INTO lhb_feature_snapshot (snapshot_id, revision, as_of, available_at,"
        " subject_type, subject_id, window_days, model_version, sample_size, source,"
        " ingested_at, content_hash, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            snapshot_id,
            revision,
            as_of,
            as_of,
            profile["subject_type"],
            profile["subject_id"],
            int(profile["window_days"]),
            str(profile.get("model_version") or "lhb-features-v1"),
            profile.get("sample_size"),
            source,
            now,
            digest,
            payload,
        ),
    )
    return snapshot_id


def load_profile_snapshot(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    as_of: str,
    window_days: int = 60,
) -> dict[str, Any] | None:
    require_available_at(as_of)
    row = conn.execute(
        "SELECT payload_json FROM lhb_feature_snapshot"
        " WHERE subject_type=? AND subject_id=? AND window_days=? AND available_at<=?"
        " ORDER BY revision DESC LIMIT 1",
        (subject_type, subject_id, window_days, as_of),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def save_signal_observation(
    conn: sqlite3.Connection,
    observation: dict[str, Any],
    *,
    source: str = "lhb-signal",
) -> str:
    payload = canonical_json(observation)
    digest = content_hash_for(observation)
    obs_id = str(
        observation.get("observation_id")
        or f"{observation['ts_code']}:{observation.get('disclose_date')}"
    )
    revision = _next_revision(
        conn, "lhb_signal_observation", "observation_id=?", (obs_id,), digest
    )
    if revision is None:
        return obs_id
    now = _now()
    conn.execute(
        "INSERT INTO lhb_signal_observation (observation_id, revision, ts_code, signal_date,"
        " disclose_at, earliest_executable_at, status, research_only, scores_json,"
        " veto_codes_json, policy_version, data_version, identity_version,"
        " feature_snapshot_id, source, available_at, ingested_at, content_hash, payload_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            obs_id,
            revision,
            observation["ts_code"],
            observation.get("disclose_date") or observation.get("signal_date"),
            observation["disclose_at"],
            observation["earliest_executable_at"],
            observation["status"],
            1,
            canonical_json(observation.get("scores") or {}),
            canonical_json(observation.get("vetoes") or []),
            observation.get("policy_version") or POLICY_VERSION_DEFAULT,
            observation.get("data_version") or "d1",
            observation.get("identity_version") or "i1",
            observation.get("feature_snapshot_id"),
            source,
            observation.get("available_at") or observation["disclose_at"],
            now,
            digest,
            payload,
        ),
    )
    return obs_id


def save_signal_outcome(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    horizon_days: int,
    status: str,
    entry_fillable: int | None,
    gross_return: float | None,
    net_return: float | None,
    benchmark_excess: float | None,
    available_at: str,
    source: str = "lhb-shadow",
) -> None:
    payload = {
        "observation_id": observation_id,
        "horizon_days": horizon_days,
        "status": status,
        "entry_fillable": entry_fillable,
        "gross_return": gross_return,
        "net_return": net_return,
        "benchmark_excess": benchmark_excess,
    }
    digest = content_hash_for(payload)
    revision = _next_revision(
        conn,
        "lhb_signal_outcome",
        "observation_id=? AND horizon_days=?",
        (observation_id, horizon_days),
        digest,
    )
    if revision is None:
        return
    now = _now()
    conn.execute(
        "INSERT INTO lhb_signal_outcome (observation_id, horizon_days, revision, status,"
        " entry_fillable, gross_return, net_return, benchmark_excess, source, available_at,"
        " ingested_at, content_hash, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            observation_id,
            horizon_days,
            revision,
            status,
            entry_fillable,
            gross_return,
            net_return,
            benchmark_excess,
            source,
            available_at,
            now,
            digest,
            canonical_json(payload),
        ),
    )


def list_events(
    conn: sqlite3.Connection,
    *,
    trade_date: str | None = None,
    ts_code: str | None = None,
    seat_id: str | None = None,
    actor_type: str | None = None,
    min_confidence: float | None = None,
    as_of: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cutoff = require_available_at(as_of or "9999-12-31T23:59:59+08:00")
    where: list[str] = []
    params: list[Any] = []
    if trade_date:
        parse_trade_date(trade_date)
        where.append("disclose_date=?")
        params.append(trade_date)
    if ts_code:
        where.append("ts_code=?")
        params.append(ts_code)
    where.append("available_at<=?")
    params.append(cutoff)
    sql = (
        "WITH ranked AS (SELECT event_id,revision,exchange,ts_code,window_code,reason_code,"
        " reason_raw,disclose_date,source,source_status,available_at,content_hash,payload_json,"
        " ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY revision DESC,available_at DESC) rn"
        " FROM lhb_event"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ") SELECT event_id,revision,exchange,ts_code,window_code,reason_code,reason_raw,"
    sql += " disclose_date,source,source_status,available_at,content_hash,payload_json FROM ranked"
    sql += " WHERE rn=1 ORDER BY disclose_date DESC,ts_code,event_id"
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row[12]) if row[12] else {}
        out.append(
            {
                "event_id": row[0],
                "revision": row[1],
                "exchange": row[2],
                "ts_code": row[3],
                "window_code": row[4],
                "reason_code": row[5],
                "reason_raw": row[6],
                "disclose_date": row[7],
                "source": row[8],
                "source_status": row[9],
                "available_at": row[10],
                "content_hash": row[11],
                "payload": payload,
            }
        )
    if seat_id or actor_type or min_confidence is not None:
        filtered_ids = _filtered_event_ids(
            conn,
            event_rows=out,
            cutoff=cutoff,
            seat_id=seat_id,
            actor_type=actor_type,
            min_confidence=min_confidence,
        )
        out = [item for item in out if item["event_id"] in filtered_ids]
    return out[offset : offset + limit]


def _filtered_event_ids(
    conn: sqlite3.Connection,
    *,
    event_rows: list[dict[str, Any]],
    cutoff: str,
    seat_id: str | None,
    actor_type: str | None,
    min_confidence: float | None,
) -> set[str]:
    """高级筛选在 PIT 可见的最新席位事实/身份假设上执行。"""
    if not event_rows:
        return set()
    event_dates = {str(row["event_id"]): str(row["disclose_date"]) for row in event_rows}
    placeholders = ",".join("?" for _ in event_dates)
    trades = conn.execute(
        "WITH ranked AS (SELECT event_id,seat_raw,seat_id,revision,available_at,"
        " ROW_NUMBER() OVER (PARTITION BY event_id,seat_raw ORDER BY revision DESC) rn"
        f" FROM lhb_seat_trade WHERE event_id IN ({placeholders}) AND available_at<=?)"
        " SELECT event_id,seat_raw,seat_id FROM ranked WHERE rn=1",
        (*event_dates, cutoff),
    ).fetchall()
    matched: set[str] = set()
    for event_id, seat_raw, mapped_seat_id in trades:
        sid = str(mapped_seat_id or seat_raw)
        if seat_id and seat_id not in {sid, str(seat_raw)}:
            continue
        if actor_type is None and min_confidence is None:
            matched.add(str(event_id))
            continue
        day = event_dates[str(event_id)]
        row = conn.execute(
            "WITH hp AS (SELECT h.*,ROW_NUMBER() OVER (PARTITION BY h.seat_id,h.actor_id,h.valid_from"
            " ORDER BY h.revision DESC) rn FROM seat_actor_hypothesis h"
            " WHERE h.seat_id=? AND h.valid_from<=? AND (h.valid_to IS NULL OR h.valid_to>?)"
            " AND h.available_at<=?), am AS (SELECT a.*,ROW_NUMBER() OVER (PARTITION BY a.actor_id"
            " ORDER BY a.revision DESC) rn FROM actor_master a WHERE a.valid_from<=?"
            " AND (a.valid_to IS NULL OR a.valid_to>?) AND a.available_at<=?)"
            " SELECT hp.confidence,am.actor_type FROM hp JOIN am ON am.actor_id=hp.actor_id"
            " WHERE hp.rn=1 AND am.rn=1 ORDER BY hp.confidence DESC LIMIT 1",
            (sid, day, day, cutoff, day, day, cutoff),
        ).fetchone()
        if row is None:
            continue
        if actor_type is not None and str(row[1]) != actor_type:
            continue
        if min_confidence is not None and float(row[0]) < min_confidence:
            continue
        matched.add(str(event_id))
    return matched


def list_signals(
    conn: sqlite3.Connection,
    *,
    trade_date: str | None = None,
    ts_code: str | None = None,
    status: str | None = None,
    as_of: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cutoff = require_available_at(as_of or "9999-12-31T23:59:59+08:00")
    where: list[str] = []
    params: list[Any] = []
    if trade_date:
        where.append("signal_date=?")
        params.append(trade_date)
    if ts_code:
        where.append("ts_code=?")
        params.append(ts_code)
    if status:
        where.append("status=?")
        params.append(status)
    where.append("available_at<=?")
    params.append(cutoff)
    sql = (
        "WITH ranked AS (SELECT observation_id, revision, ts_code, signal_date, disclose_at,"
        " earliest_executable_at, status, scores_json, veto_codes_json, policy_version,"
        " data_version, identity_version, available_at, payload_json,"
        " ROW_NUMBER() OVER (PARTITION BY observation_id ORDER BY revision DESC,available_at DESC) rn"
        " FROM lhb_signal_observation"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ") SELECT observation_id,revision,ts_code,signal_date,disclose_at,"
    sql += " earliest_executable_at,status,scores_json,veto_codes_json,policy_version,"
    sql += " data_version,identity_version,available_at,payload_json FROM ranked WHERE rn=1"
    sql += " ORDER BY signal_date DESC,ts_code LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "observation_id": row[0],
                "revision": row[1],
                "ts_code": row[2],
                "signal_date": row[3],
                "disclose_at": row[4],
                "earliest_executable_at": row[5],
                "status": row[6],
                "scores": json.loads(row[7]),
                "vetoes": json.loads(row[8]),
                "policy_version": row[9],
                "data_version": row[10],
                "identity_version": row[11],
                "available_at": row[12],
                "research_only": True,
                "payload": json.loads(row[13]) if row[13] else {},
            }
        )
    return out


def quality_summary(conn: sqlite3.Connection, trade_date: str) -> dict[str, Any]:
    parse_trade_date(trade_date)
    status = partition_status(conn, trade_date)
    recon_open = 0
    if _table_exists(conn, "lhb_reconciliation"):
        row = conn.execute(
            "SELECT COUNT(*) FROM lhb_reconciliation WHERE trade_date=? AND status='OPEN'",
            (trade_date,),
        ).fetchone()
        recon_open = int(row[0]) if row else 0
    event_n = 0
    if _table_exists(conn, "lhb_event"):
        row = conn.execute(
            "SELECT COUNT(*) FROM lhb_event WHERE disclose_date=?", (trade_date,)
        ).fetchone()
        event_n = int(row[0]) if row else 0
    return {
        "trade_date": trade_date,
        "source_status": status,
        "event_count": event_n,
        "open_recon_count": recon_open,
        "research_only": True,
    }
