"""龙虎榜跨源对账与数据质量门禁（T03）。差异逐条保留，不静默覆盖。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from ab_screener.domain.data_point import canonical_json, content_hash_for
from ab_screener.domain.lhb_contracts import (
    RECON_DIFF_VALUES,
    AmountUnit,
    normalize_top_inst_side,
    parse_enum,
    parse_trade_date,
    require_available_at,
    to_fen,
)

COVERAGE_MIN_PCT = 98.0
RECON_MATCH_MIN_PCT = 90.0
UNKNOWN_SEAT_MAX_PCT = 40.0
MISSING_FIELD_MAX_PCT = 5.0


def calendar_coverage(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    calendar_open_dates: Iterable[str],
) -> dict[str, Any]:
    """覆盖率按交易日历开市日计算，周末/节假日不计入缺日。"""
    open_days = [parse_trade_date(d) for d in calendar_open_dates]
    done = {
        str(row[0])
        for row in conn.execute(
            "SELECT partition_key FROM pit_backfill_checkpoints"
            " WHERE dataset=? AND status='done'",
            (dataset,),
        )
    }
    missing = [d for d in open_days if d not in done]
    total = len(open_days)
    covered = total - len(missing)
    pct = (100.0 * covered / total) if total else 0.0
    return {
        "dataset": dataset,
        "open_days": total,
        "done": covered,
        "missing": missing,
        "pct": pct,
    }


def _row_side(row: dict[str, Any]) -> str:
    return normalize_top_inst_side(row.get("side"), buy=row.get("buy"), sell=row.get("sell"))


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _collapse_duplicate_side_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """同席位同原因同 side 多行：取该方向金额，不把重复行相加。"""
    if not rows:
        return None
    if len(rows) == 1:
        return dict(rows[0])
    base = dict(rows[0])
    side = _row_side(base)
    if side == "BUY":
        base["buy"] = max(_num(r.get("buy")) for r in rows)
        base["sell"] = 0.0
        base["net_buy"] = base["buy"]
    elif side == "SELL":
        base["sell"] = max(_num(r.get("sell")) for r in rows)
        base["buy"] = 0.0
        base["net_buy"] = -base["sell"]
    else:
        base["buy"] = max(_num(r.get("buy")) for r in rows)
        base["sell"] = max(_num(r.get("sell")) for r in rows)
        base["net_buy"] = base["buy"] - base["sell"]
    base["side"] = side
    return base


def _index_rows(
    rows: Iterable[dict[str, Any]], keys: tuple[str, ...]
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    """同一席位买卖双榜必须分行保留，禁止后写覆盖。"""
    out: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(
            _row_side(row) if k == "side" else str(row.get(k) or "")
            for k in keys
        )
        out.setdefault(key, []).append(row)
    return out


def reconcile_sources(
    *,
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    left_source: str,
    right_source: str,
    trade_date: str,
    keys: tuple[str, ...] = ("ts_code", "exalter", "reason", "side"),
    amount_fields: tuple[str, ...] = ("buy", "sell", "net_buy"),
    left_unit: AmountUnit | str,
    right_unit: AmountUnit | str,
) -> list[dict[str, Any]]:
    """比较两侧原始值，生成对账差异（不修改任一侧）。金额单位必须显式给出。"""
    parse_trade_date(trade_date)
    left_u = left_unit if isinstance(left_unit, AmountUnit) else AmountUnit(left_unit)
    right_u = right_unit if isinstance(right_unit, AmountUnit) else AmountUnit(right_unit)
    left_idx = _index_rows(left_rows, keys)
    right_idx = _index_rows(right_rows, keys)
    diffs: list[dict[str, Any]] = []
    for key in sorted(set(left_idx) | set(right_idx)):
        left_list = left_idx.get(key) or []
        right_list = right_idx.get(key) or []
        left = _collapse_duplicate_side_rows(left_list)
        right = _collapse_duplicate_side_rows(right_list)
        ts_code = key[0] if key else ""
        if left is None:
            diffs.append(
                _diff(
                    trade_date,
                    ts_code,
                    "row",
                    left_source,
                    None,
                    right_source,
                    right,
                    "MISSING_LEFT",
                    locator=dict(zip(keys, key, strict=True)),
                )
            )
            continue
        if right is None:
            diffs.append(
                _diff(
                    trade_date,
                    ts_code,
                    "row",
                    left_source,
                    left,
                    right_source,
                    None,
                    "MISSING_RIGHT",
                    locator=dict(zip(keys, key, strict=True)),
                )
            )
            continue
        if str(left.get("reason") or "") != str(right.get("reason") or ""):
            diffs.append(
                _diff(trade_date, ts_code, "reason", left_source, left.get("reason"),
                      right_source, right.get("reason"), "REASON",
                      locator=dict(zip(keys, key, strict=True)))
            )
        for field in amount_fields:
            lv, rv = left.get(field), right.get(field)
            if _amount_fen(lv, left_u) != _amount_fen(rv, right_u):
                diffs.append(
                    _diff(
                        trade_date,
                        ts_code,
                        field,
                        left_source,
                        lv,
                        right_source,
                        rv,
                        "AMOUNT",
                        locator=dict(zip(keys, key, strict=True)),
                    )
                )
    return diffs


def _amount_fen(value: Any, unit: AmountUnit) -> int:
    if value in (None, ""):
        return 0
    return to_fen(value, unit)


def _diff(
    trade_date: str,
    ts_code: str,
    field_name: str,
    left_source: str,
    left_value: Any,
    right_source: str,
    right_value: Any,
    diff_type: str,
    *,
    locator: dict[str, str],
) -> dict[str, Any]:
    parse_enum(diff_type, RECON_DIFF_VALUES, label="diff_type")
    return {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "field_name": field_name,
        "left_source": left_source,
        "left_value": None if left_value is None else str(left_value),
        "right_source": right_source,
        "right_value": None if right_value is None else str(right_value),
        "diff_type": diff_type,
        "status": "OPEN",
        "locator": locator,
    }


def persist_diffs(
    conn: sqlite3.Connection,
    diffs: list[dict[str, Any]],
    *,
    available_at: str,
    source: str = "reconcile",
) -> int:
    """相同差异重跑跳过；内容变化才追加新 revision。"""
    available = require_available_at(available_at)
    inserted = 0
    for item in diffs:
        recon_id = content_hash_for(
            {
                "trade_date": item["trade_date"],
                "ts_code": item["ts_code"],
                "field_name": item["field_name"],
                "diff_type": item["diff_type"],
                "left_source": item["left_source"],
                "right_source": item["right_source"],
                "locator": item.get("locator") or {},
            }
        )
        digest = content_hash_for(item)
        prev = conn.execute(
            "SELECT revision, content_hash FROM lhb_reconciliation"
            " WHERE recon_id=? ORDER BY revision DESC LIMIT 1",
            (recon_id,),
        ).fetchone()
        if prev and prev[1] == digest:
            continue
        revision = int(prev[0]) + 1 if prev else 1
        payload = canonical_json(item)
        conn.execute(
            "INSERT INTO lhb_reconciliation (recon_id, revision, trade_date, ts_code, field_name,"
            " left_source, left_value, right_source, right_value, diff_type, status, source,"
            " available_at, ingested_at, content_hash, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                recon_id,
                revision,
                item["trade_date"],
                item["ts_code"],
                item["field_name"],
                item["left_source"],
                item["left_value"],
                item["right_source"],
                item["right_value"],
                item["diff_type"],
                item["status"],
                source,
                available,
                available,
                digest,
                payload,
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def manifest_exists(conn: sqlite3.Connection, *, dataset: str, partition_key: str) -> bool:
    """统一证据链：lhb_ingest_manifests 与 raw_ingest_manifests 任一即可。"""
    for table in ("lhb_ingest_manifests", "raw_ingest_manifests"):
        if not _table_exists(conn, table):
            continue
        row = conn.execute(
            f"SELECT manifest_id FROM {table} WHERE dataset=? AND partition_key=?",
            (dataset, partition_key),
        ).fetchone()
        if row:
            return True
    return False


def trace_to_manifest(
    conn: sqlite3.Connection,
    *,
    n_days: int = 20,
    n_seats: int = 20,
) -> dict[str, Any]:
    """从 top_inst_history 抽样追溯 ingest manifest（两条链合一）。"""
    days = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trade_date FROM top_inst_history ORDER BY trade_date LIMIT ?",
            (n_days,),
        )
    ]
    seats = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT exalter FROM top_inst_history ORDER BY exalter LIMIT ?",
            (n_seats,),
        )
    ]
    traced = 0
    missing = 0
    for day in days:
        if manifest_exists(conn, dataset="top_inst", partition_key=day):
            traced += 1
        else:
            missing += 1
    seat_traced = 0
    for seat in seats:
        row = conn.execute(
            "SELECT trade_date FROM top_inst_history WHERE exalter=? LIMIT 1",
            (seat,),
        ).fetchone()
        if not row:
            continue
        if manifest_exists(conn, dataset="top_inst", partition_key=row[0]):
            seat_traced += 1
    return {
        "days_requested": n_days,
        "days_found": len(days),
        "days_traced": traced,
        "days_missing_manifest": missing,
        "seats_requested": n_seats,
        "seats_found": len(seats),
        "seats_traced": seat_traced,
        "pass": traced == len(days) and len(days) >= n_days and seat_traced >= min(n_seats, len(seats)),
    }


def quality_gate(
    *,
    coverage_pct: float,
    recon_match_pct: float,
    unknown_seat_pct: float = 0.0,
    missing_field_pct: float = 0.0,
) -> dict[str, Any]:
    blockers: list[str] = []
    if coverage_pct < COVERAGE_MIN_PCT:
        blockers.append("coverage")
    if recon_match_pct < RECON_MATCH_MIN_PCT:
        blockers.append("recon")
    if unknown_seat_pct > UNKNOWN_SEAT_MAX_PCT:
        blockers.append("unknown_seat")
    if missing_field_pct > MISSING_FIELD_MAX_PCT:
        blockers.append("missing_field")
    result = "INSUFFICIENT" if blockers else "PASS"
    return {
        "result": result,
        "blockers": blockers,
        "allows_confirmed_signal": result == "PASS",
        "coverage_pct": coverage_pct,
        "recon_match_pct": recon_match_pct,
    }
