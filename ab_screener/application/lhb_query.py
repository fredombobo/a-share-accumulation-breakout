"""龙虎榜只读查询（T10）。空/未发布/失败用 source_status 区分，不用空数组掩盖。"""
from __future__ import annotations

from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.db import SchemaMissing, connect, table_exists
from ab_screener.data.lhb_repository import (
    envelope,
    list_events,
    list_signals,
    load_profile_snapshot,
    partition_status,
    quality_summary,
)
from ab_screener.domain.lhb_contracts import parse_trade_date, require_available_at

CONFIDENCE_LABEL = {
    "A": "证据较强（仍为假设，不是实名）",
    "B": "候选假设",
    "C": "低置信，不得使用确定语气",
}
_TZ = ZoneInfo("Asia/Shanghai")


def _query_now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def coerce_as_of(value: str) -> str:
    """还原未编码的 ``+``，并统一成可安全字符串比较的上海时区。"""
    if " " in value and "+" not in value:
        value = value.replace(" ", "+", 1)
    return require_available_at(value)


def _as_of_from_date(trade_date: str) -> str:
    parse_trade_date(trade_date)
    return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T16:00:00+08:00"


def _open(db_path: str | Path):
    return connect(Path(db_path), readonly=True)


def radar(
    db_path: str | Path,
    trade_date: str,
    *,
    seat_id: str | None = None,
    actor_type: str | None = None,
    min_confidence: float | None = None,
    as_of: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    as_of = coerce_as_of(as_of) if as_of else _query_now()
    try:
        with _open(db_path) as conn:
            if not table_exists(conn, "lhb_event"):
                return envelope(
                    source_status="FETCH_FAILED",
                    as_of=as_of,
                    error_reason="SCHEMA_MISSING",
                )
            status = partition_status(conn, trade_date)
            items = list_events(
                conn,
                trade_date=trade_date,
                seat_id=seat_id,
                actor_type=actor_type,
                min_confidence=min_confidence,
                as_of=as_of,
                limit=limit,
                offset=offset,
            )
            if status == "FETCH_FAILED":
                return envelope(
                    source_status="FETCH_FAILED",
                    items=items,
                    as_of=as_of,
                    error_reason="INGEST_FAILED",
                )
            if status == "NOT_PUBLISHED":
                return envelope(source_status="NOT_PUBLISHED", items=[], as_of=as_of)
            if status == "VALID_EMPTY":
                return envelope(source_status="VALID_EMPTY", items=[], as_of=as_of)
            if status == "DEGRADED":
                return envelope(source_status="DEGRADED", items=items, as_of=as_of)
            return envelope(source_status="COMPLETE", items=items, as_of=as_of)
    except SchemaMissing:
        return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")


def events(
    db_path: str | Path,
    *,
    trade_date: str | None = None,
    ts_code: str | None = None,
    seat_id: str | None = None,
    actor_type: str | None = None,
    min_confidence: float | None = None,
    as_of: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    as_of = coerce_as_of(as_of) if as_of else _query_now()
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_event"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        items = list_events(
            conn,
            trade_date=trade_date,
            ts_code=ts_code,
            seat_id=seat_id,
            actor_type=actor_type,
            min_confidence=min_confidence,
            as_of=as_of,
            limit=limit,
            offset=offset,
        )
        status = partition_status(conn, trade_date) if trade_date else ("COMPLETE" if items else "NOT_PUBLISHED")
        return envelope(source_status=status, items=items, as_of=as_of)


def seat_profile(
    db_path: str | Path,
    seat_id: str,
    *,
    as_of: str,
    window_days: int = 60,
) -> dict[str, Any]:
    as_of = coerce_as_of(as_of)
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_feature_snapshot"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        snap = load_profile_snapshot(
            conn, subject_type="seat", subject_id=seat_id, as_of=as_of, window_days=window_days
        )
        if snap is None:
            return envelope(source_status="VALID_EMPTY", as_of=as_of, extra={"subject_id": seat_id})
        hyp = None
        if table_exists(conn, "seat_actor_hypothesis"):
            day = as_of[:10].replace("-", "")
            row = conn.execute(
                "WITH ranked AS (SELECT actor_id,confidence,evidence_grade,hypothesis_note,"
                " conflict_status,revision,ROW_NUMBER() OVER (PARTITION BY actor_id,valid_from"
                " ORDER BY revision DESC) rn FROM seat_actor_hypothesis"
                " WHERE seat_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
                " AND available_at<=?) SELECT actor_id,confidence,evidence_grade,hypothesis_note,"
                " conflict_status FROM ranked WHERE rn=1 ORDER BY confidence DESC,actor_id LIMIT 1",
                (seat_id, day, day, as_of),
            ).fetchone()
            if row:
                hyp = {
                    "actor_id": row[0],
                    "confidence": row[1],
                    "evidence_grade": row[2],
                    "note": row[3],
                    "conflict_status": row[4],
                    "identity_language": CONFIDENCE_LABEL.get(str(row[2]), CONFIDENCE_LABEL["C"]),
                }
        snap = dict(snap)
        snap["identity"] = hyp
        return envelope(source_status="COMPLETE", items=[snap], as_of=as_of, extra={"subject_id": seat_id})


def stock_timeline(db_path: str | Path, ts_code: str, *, limit: int = 100) -> dict[str, Any]:
    as_of = "1970-01-01T00:00:00+08:00"
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_event"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        items = list_events(conn, ts_code=ts_code, limit=limit)
        if items:
            as_of = str(items[0].get("available_at") or as_of)
        status = "COMPLETE" if items else "VALID_EMPTY"
        return envelope(source_status=status, items=items, as_of=as_of, extra={"ts_code": ts_code})


def actor_profile(
    db_path: str | Path, actor_id: str, *, as_of: str, window_days: int = 60
) -> dict[str, Any]:
    as_of = coerce_as_of(as_of)
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_feature_snapshot"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        snap = load_profile_snapshot(
            conn, subject_type="actor", subject_id=actor_id, as_of=as_of, window_days=window_days
        )
        if snap is None:
            return envelope(source_status="VALID_EMPTY", as_of=as_of, extra={"subject_id": actor_id})
        return envelope(source_status="COMPLETE", items=[snap], as_of=as_of)


def network(db_path: str | Path, *, trade_date: str, as_of: str | None = None) -> dict[str, Any]:
    as_of = coerce_as_of(as_of) if as_of else _query_now()
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_seat_trade"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        event_rows = list_events(conn, trade_date=trade_date, as_of=as_of, limit=10_000)
        status = partition_status(conn, trade_date)
        if not event_rows:
            return envelope(source_status=status, as_of=as_of, extra={"nodes": []})
        event_to_code = {str(row["event_id"]): str(row["ts_code"]) for row in event_rows}
        placeholders = ",".join("?" for _ in event_to_code)
        trades = conn.execute(
            "WITH ranked AS (SELECT event_id,seat_raw,seat_id,net_amount_fen,revision,"
            " ROW_NUMBER() OVER (PARTITION BY event_id,seat_raw ORDER BY revision DESC) rn"
            f" FROM lhb_seat_trade WHERE event_id IN ({placeholders}) AND available_at<=?)"
            " SELECT event_id,seat_raw,seat_id,net_amount_fen FROM ranked WHERE rn=1",
            (*event_to_code, as_of),
        ).fetchall()
        actors_by_code: dict[str, set[str]] = {}
        labels: dict[str, str] = {}
        net_by_actor: dict[str, int] = {}
        for event_id, seat_raw, mapped_seat_id, net_fen in trades:
            code = event_to_code[str(event_id)]
            sid = str(mapped_seat_id or seat_raw)
            day = trade_date
            hyp = conn.execute(
                "WITH ranked AS (SELECT actor_id,confidence,evidence_grade,hypothesis_note,revision,"
                " ROW_NUMBER() OVER (PARTITION BY actor_id,valid_from ORDER BY revision DESC) rn"
                " FROM seat_actor_hypothesis WHERE seat_id=? AND valid_from<=?"
                " AND (valid_to IS NULL OR valid_to>?) AND available_at<=?)"
                " SELECT actor_id,hypothesis_note FROM ranked WHERE rn=1"
                " ORDER BY confidence DESC,actor_id LIMIT 1",
                (sid, day, day, as_of),
            ).fetchone()
            actor_id = str(hyp[0]) if hyp else f"seat:{sid}"
            labels[actor_id] = str(hyp[1] or actor_id) if hyp else str(seat_raw)
            actors_by_code.setdefault(code, set()).add(actor_id)
            net_by_actor[actor_id] = net_by_actor.get(actor_id, 0) + int(net_fen)
        weights: dict[tuple[str, str], int] = {}
        stocks: dict[tuple[str, str], list[str]] = {}
        for code, actors in actors_by_code.items():
            for left, right in combinations(sorted(actors), 2):
                key = (left, right)
                weights[key] = weights.get(key, 0) + 1
                stocks.setdefault(key, []).append(code)
        items = [
            {
                "source_actor_id": left,
                "target_actor_id": right,
                "weight": weight,
                "ts_codes": sorted(stocks[(left, right)]),
            }
            for (left, right), weight in sorted(weights.items(), key=lambda item: (-item[1], item[0]))
        ]
        nodes = [
            {
                "actor_id": actor,
                "label": labels.get(actor, actor),
                "net_yuan": net_by_actor.get(actor, 0) / 100.0,
                "stock_count": sum(actor in members for members in actors_by_code.values()),
            }
            for actor in sorted(labels)
        ]
        return envelope(
            source_status=status,
            items=items,
            as_of=as_of,
            extra={
                "nodes": nodes,
                "independent_actor_count": len(nodes),
                "method_note": "同一 actor 多席位在同股同日只计 1 个主体",
            },
        )


def quality(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    as_of = _as_of_from_date(trade_date)
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_ingest_manifests"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        summary = quality_summary(conn, trade_date)
        return envelope(
            source_status=str(summary["source_status"]),
            items=[summary],
            as_of=as_of,
        )


def signals(
    db_path: str | Path,
    *,
    trade_date: str | None = None,
    ts_code: str | None = None,
    status: str | None = None,
    as_of: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    as_of = coerce_as_of(as_of) if as_of else (
        _as_of_from_date(trade_date) if trade_date else _query_now()
    )
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_signal_observation"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        items = list_signals(
            conn,
            trade_date=trade_date,
            ts_code=ts_code,
            status=status,
            as_of=as_of,
            limit=limit,
            offset=offset,
        )
        status_code = "COMPLETE" if items else "VALID_EMPTY"
        return envelope(source_status=status_code, items=items, as_of=as_of)


def backtest_summary(db_path: str | Path) -> dict[str, Any]:
    as_of = _query_now()
    with _open(db_path) as conn:
        if not table_exists(conn, "lhb_signal_outcome"):
            return envelope(source_status="FETCH_FAILED", as_of=as_of, error_reason="SCHEMA_MISSING")
        rows = conn.execute(
            "WITH ranked AS (SELECT o.*,ROW_NUMBER() OVER (PARTITION BY observation_id,horizon_days"
            " ORDER BY revision DESC) rn FROM lhb_signal_outcome o WHERE available_at<=?)"
            " SELECT horizon_days,status,gross_return,net_return,benchmark_excess,available_at"
            " FROM ranked WHERE rn=1 ORDER BY horizon_days,observation_id",
            (as_of,),
        ).fetchall()
        observations = conn.execute(
            "SELECT COUNT(DISTINCT observation_id),MIN(signal_date),MAX(signal_date)"
            " FROM lhb_signal_observation"
        ).fetchone()
        by_horizon: dict[str, dict[str, Any]] = {}
        matured_ids = 0
        for horizon in (1, 3, 5, 10, 20):
            bucket = [row for row in rows if int(row[0]) == horizon]
            matured = [row for row in bucket if row[1] == "MATURED"]
            if horizon == 1:
                matured_ids = len(matured)
            by_horizon[str(horizon)] = {
                "observations": len(bucket),
                "matured": len(matured),
                "unfillable": sum(row[1] == "UNFILLABLE" for row in bucket),
                "gross_return": _mean([row[2] for row in matured]),
                "net_return": _mean([row[3] for row in matured]),
                "benchmark_excess": _mean([row[4] for row in matured]),
            }
        count = int(observations[0] or 0) if observations else 0
        extra = {
            "engineering_pass_is_not_edge": True,
            "research_status": "RESEARCH_BLOCKED",
            "can_claim_edge": False,
            "shadow_maturity": "insufficient" if matured_ids < 30 else "sample_only_not_promoted",
            "observation_count": count,
            "matured_independent_signals": matured_ids,
            "first_signal_date": observations[1] if observations else None,
            "last_signal_date": observations[2] if observations else None,
            "horizons": by_horizon,
            "blockers": ["ANTI_OVERFIT_NOT_PASSED", "CAPACITY_NOT_VALIDATED", "SHADOW_NOT_PROMOTED"],
        }
        return envelope(
            source_status="COMPLETE" if count or rows else "VALID_EMPTY",
            as_of=as_of,
            extra=extra,
        )


def _mean(values: list[Any]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return (sum(usable) / len(usable)) if usable else None
