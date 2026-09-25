"""Prospective price observations in an isolated, append-only sidecar.

The market database is always read-only. No orders, migrations, data refreshes,
backfills or performance claims are performed here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.trading_calendar import VERIFIED_CALENDAR_SOURCES, date_key
from ab_screener.domain.data_point import content_hash_for

VERSION = "forward-price-observation-v3"
HORIZONS = (1, 5, 10, 20)
NOTE = "扫描日收盘至后续交易日收盘的价格观察，不是成交收益；未计费用、滑点、仓位或可成交性。"
_TZ = ZoneInfo("Asia/Shanghai")


class ForwardObservationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _clock(now: datetime | None = None) -> datetime:
    value = now or datetime.now(_TZ)
    return value.replace(tzinfo=_TZ) if value.tzinfo is None else value.astimezone(_TZ)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _parse_stamp(value: str | None) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result.astimezone(_TZ)
    except (ValueError, TypeError) as exc:
        raise ForwardObservationError("TIMESTAMP_UNVERIFIED", "扫描起止时刻缺失或未带时区") from exc


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def sidecar_path(db_path: str | Path) -> Path:
    market = Path(db_path).resolve()
    result = market.with_name("forward_observations.db")
    if result == market or (result.exists() and market.exists() and result.samefile(market)):
        raise ForwardObservationError("SIDECAR_PATH_CONFLICT", "观察数据库必须与行情数据库独立")
    return result


def _read(path: Path):
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return closing(connection)


def _write(path: Path):
    # Only enable creates the sidecar; capture/refresh never create a missing DB.
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    return closing(connection)


def _protocol(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT payload_json FROM forward_protocol WHERE singleton=1").fetchone()
    return json.loads(row[0]) if row else None


def _require_current_protocol(protocol: dict) -> None:
    if protocol.get("version") != VERSION:
        raise ForwardObservationError("PROTOCOL_VERSION_MISMATCH", "观察协议版本不同，保留旧记录并拒绝混算")


def _require_market_binding(protocol: dict | None, db_path: str | Path) -> None:
    if protocol and protocol.get("market_database") != str(Path(db_path).resolve()):
        raise ForwardObservationError("SIDECAR_MARKET_MISMATCH", "观察库绑定了另一行情数据库")


def enable_forward_observations(db_path: str | Path, *, now: datetime | None = None) -> dict:
    market = Path(db_path).resolve()
    if not market.is_file():
        raise ForwardObservationError("MARKET_DATABASE_MISSING", "行情数据库不存在")
    path = sidecar_path(market)
    clock = _clock(now)
    with closing(sqlite3.connect(path, timeout=15)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS forward_protocol(singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forward_captures(run_id TEXT PRIMARY KEY,as_of TEXT NOT NULL,cohort_key TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('PRIMARY','SECONDARY')),captured_at TEXT NOT NULL,payload_json TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS forward_one_primary ON forward_captures(cohort_key) WHERE role='PRIMARY';
        CREATE TABLE IF NOT EXISTS forward_candidates(run_id TEXT NOT NULL,ts_code TEXT NOT NULL,pool TEXT NOT NULL,
          observation_group TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,ts_code));
        CREATE TABLE IF NOT EXISTS forward_baselines(run_id TEXT NOT NULL,ts_code TEXT NOT NULL,
          payload_json TEXT NOT NULL,PRIMARY KEY(run_id,ts_code));
        CREATE TABLE IF NOT EXISTS forward_outcomes(run_id TEXT NOT NULL,ts_code TEXT NOT NULL,horizon INTEGER NOT NULL,
          revision INTEGER NOT NULL,business_hash TEXT NOT NULL,computed_at TEXT NOT NULL,payload_json TEXT NOT NULL,
          PRIMARY KEY(run_id,ts_code,horizon,revision));
        CREATE TABLE IF NOT EXISTS forward_attempts(id INTEGER PRIMARY KEY,run_id TEXT,operation TEXT NOT NULL,
          status TEXT NOT NULL,occurred_at TEXT NOT NULL,payload_json TEXT NOT NULL);
        """)
        for table in ("forward_protocol", "forward_captures", "forward_candidates", "forward_baselines", "forward_outcomes", "forward_attempts"):
            for action in ("UPDATE", "DELETE"):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} "
                             f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
        conn.execute("BEGIN IMMEDIATE")
        existing = _protocol(conn)
        if existing is None:
            protocol = {"version": VERSION, "enabled_at": _stamp(clock), "horizons": list(HORIZONS),
                        "metric": "price_observation", "baseline": "scan_day_close_frozen_at_capture",
                        "restatement": "separate_latest_source_comparison_never_replaces_captured_baseline",
                        "primary_rule": "first_eligible_publication_per_date_entry_and_qualification_semantics",
                        "capture_deadline": "next_exchange_auction_09:15_Asia/Shanghai",
                        "maturity_time": "target_session_16:00_Asia/Shanghai",
                        "adjustment": "exact_date_factors_required_for_both_endpoints",
                        "market_database": str(market), "note": NOTE}
            conn.execute("INSERT INTO forward_protocol VALUES (1,?)", (_json(protocol),))
        else:
            protocol = existing
            _require_current_protocol(protocol)
            if protocol["market_database"] != str(market):
                raise ForwardObservationError("SIDECAR_MARKET_MISMATCH", "观察库绑定了另一行情数据库")
        conn.commit()
    return forward_status(market)


def _attempt(conn, *, operation: str, run_id: str | None, status: str, clock: datetime, **details) -> dict:
    result = {"operation": operation, "run_id": run_id, "status": status, "occurred_at": _stamp(clock), **details}
    conn.execute("INSERT INTO forward_attempts(run_id,operation,status,occurred_at,payload_json) VALUES (?,?,?,?,?)",
                 (run_id, operation, status, _stamp(clock), _json(result)))
    return result


def _calendar_targets(conn, as_of: str) -> dict[int, str | None]:
    """Each target requires contiguous authoritative open AND closed records."""
    rows = conn.execute("SELECT cal_date,is_open,source FROM trade_cal WHERE cal_date>=? ORDER BY cal_date",
                        (as_of,)).fetchall()
    calendar = {str(row[0]): (row[1], str(row[2] or "").lower()) for row in rows}
    targets = dict.fromkeys(HORIZONS)
    cursor = datetime.strptime(as_of, "%Y%m%d")
    count = 0
    for index in range(370):
        key = cursor.strftime("%Y%m%d")
        item = calendar.get(key)
        if item is None or item[0] not in (0, 1) or item[1] not in VERIFIED_CALENDAR_SOURCES:
            break
        if index == 0 and item[0] != 1:
            break
        if index > 0 and item[0] == 1:
            count += 1
            if count in targets:
                targets[count] = key
            if count == max(HORIZONS):
                break
        cursor += timedelta(days=1)
    return targets


def _group(row: dict) -> tuple[str, str]:
    match = re.search(r"\[池([AB])\|", str(row.get("reasons") or ""))
    pool = str(row.get("pool") or (match.group(1) if match else "B"))
    group = row.get("observation_group") or row.get("qualification_group")
    if group not in {"A", "B", "DATA_INCOMPLETE"}:
        group = "DATA_INCOMPLETE" if row.get("data_complete") is False else pool
    return pool, group


def _publication_evidence(publication: dict | None, protocol: dict, clock: datetime) -> dict:
    if not publication or not publication.get("verified") or not publication.get("qualified_verified"):
        raise ForwardObservationError("QUALIFIED_PUBLICATION_REQUIRED", "需要完整且验证通过的合格名单发布记录")
    fresh = publication.get("freshness") or {}
    as_of = date_key(publication["as_of"])
    if (publication.get("state") not in {"READY", "DATA_BLOCKED"} or fresh.get("historical")
            or fresh.get("calendar_verified") is not True or fresh.get("expected_as_of") != as_of):
        raise ForwardObservationError("HISTORICAL_OR_UNVERIFIED", "历史回放或未验证交易日期不能补录为前瞻")
    started = _parse_stamp(publication.get("started_at"))
    published = _parse_stamp(publication.get("completed_at"))
    if started <= _parse_stamp(protocol["enabled_at"]):
        raise ForwardObservationError("STARTED_BEFORE_ENABLE", "扫描开始时刻未晚于观察启用时刻")
    if not started <= published <= clock:
        raise ForwardObservationError("TIMESTAMP_UNVERIFIED", "扫描起止与捕获时刻顺序异常")
    qualification = publication.get("qualification") or {}
    identity = {key: publication.get(key) for key in
                ("run_id", "as_of", "config_hash", "entry_hash", "dataset_version", "result_hash", "code_version")}
    if any(not value for value in identity.values()) or not qualification.get("hash"):
        raise ForwardObservationError("IDENTITY_INCOMPLETE", "扫描或完整名单身份字段不完整")
    semantics = (qualification.get("rules_snapshot") or {}).get("semantics")
    if (not isinstance(semantics, dict) or type(semantics.get("target_count")) is not int
            or semantics["target_count"] < 1 or type(semantics.get("build_watch")) is not bool
            or not isinstance(semantics.get("box_ladder_days"), list) or not semantics["box_ladder_days"]
            or any(type(day) is not int or day < 1 for day in semantics["box_ladder_days"])):
        raise ForwardObservationError("QUALIFICATION_SEMANTICS_REQUIRED", "需要冻结实际资格规则，展示数量和来源不能替代资格语义")
    rows = publication.get("candidates")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ForwardObservationError("CANDIDATE_PAYLOAD_INVALID", "完整名单格式异常")
    if len({row.get("ts_code") for row in rows}) != len(rows):
        raise ForwardObservationError("CANDIDATE_PAYLOAD_INVALID", "完整名单股票不唯一")
    counts = {"A": 0, "B": 0, "DATA_INCOMPLETE": 0, "total": len(rows)}
    for row in rows:
        if not row.get("ts_code") or date_key(row.get("trade_date")) != as_of:
            raise ForwardObservationError("CANDIDATE_PAYLOAD_INVALID", "合格名单缺股票代码或扫描日期不一致")
        pool, group = _group(row)
        if pool not in {"A", "B"} or group not in counts or group == "total":
            raise ForwardObservationError("CANDIDATE_PAYLOAD_INVALID", "合格名单分组异常")
        counts[group] += 1
    # Only real entry/qualification semantics define repeated-scan cohorts.
    # Full config, provenance, Top limits and ladder outcomes remain evidence.
    cohort = _hash({"as_of": as_of, "entry_hash": identity["entry_hash"], "qualification_semantics": semantics})
    return {**identity, "started_at": _stamp(started), "published_at": _stamp(published),
            "qualification": qualification, "qualification_semantics": semantics, "cohort_key": cohort,
            "counts": counts, "freshness": fresh, "regime": publication.get("regime")}


def _deadline(conn, as_of: str) -> datetime:
    target = _calendar_targets(conn, as_of)[1]
    if not target:
        raise ForwardObservationError("NEXT_OPEN_UNVERIFIED", "独立日历未覆盖下一开市日")
    return datetime.combine(datetime.strptime(target, "%Y%m%d").date(), time(9, 15), _TZ)


def _first_publication(db_path, market, evidence: dict, protocol: dict, clock: datetime, deadline: datetime) -> str:
    from ab_screener.application.scan_publication import read_scan_publication

    runs = market.execute("SELECT run_id FROM scan_runs WHERE status='SUCCEEDED' AND as_of=? "
                          "ORDER BY created_at,rowid", (evidence["as_of"],)).fetchall()
    for row in runs:
        try:
            candidate = _publication_evidence(read_scan_publication(db_path, row[0], stage="qualified"), protocol, clock)
            if candidate["cohort_key"] == evidence["cohort_key"] and _parse_stamp(candidate["published_at"]) < deadline:
                return row[0]
        except (ForwardObservationError, ValueError, TypeError, KeyError):
            continue
    raise ForwardObservationError("PRIMARY_PUBLICATION_UNVERIFIED", "无法核对该组首个合格发布，不为后扫指定主样本")


def capture_scan_publication(db_path: str | Path, run_id: str, *, now: datetime | None = None) -> dict:
    """Post-commit hook: failure never changes the already published scan."""
    from ab_screener.application.scan_publication import read_scan_publication

    path = sidecar_path(db_path)
    if not path.exists():
        return {"status": "DISABLED", "run_id": run_id, "captured": False}
    clock = _clock(now)
    with _write(path) as side:
        side.execute("BEGIN IMMEDIATE")
        try:
            protocol = _protocol(side)
            if not protocol:
                raise ForwardObservationError("NOT_ENABLED", "尚未启用前瞻观察")
            _require_current_protocol(protocol)
            if protocol["market_database"] != str(Path(db_path).resolve()):
                raise ForwardObservationError("SIDECAR_MARKET_MISMATCH", "观察库绑定了另一行情数据库")
            existing = side.execute("SELECT payload_json FROM forward_captures WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                side.rollback()
                return {"status": "ALREADY_CAPTURED", "captured": True, **json.loads(existing[0])}
            publication = read_scan_publication(db_path, run_id, stage="qualified")
            evidence = _publication_evidence(publication, protocol, clock)
            as_of = evidence["as_of"]
            with _read(Path(db_path)) as market:
                deadline = _deadline(market, as_of)
                if _parse_stamp(evidence["published_at"]) >= deadline or clock >= deadline:
                    raise ForwardObservationError("LATE_CAPTURE", "已到下一交易日集合竞价09:15，不能补录为前瞻样本")
                primary_run_id = _first_publication(db_path, market, evidence, protocol, clock, deadline)
                tables = {row[0] for row in market.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                baselines = []
                for row in sorted(publication["candidates"], key=lambda item: item["ts_code"]):
                    baseline = {"run_id": run_id, "ts_code": row["ts_code"], "as_of": as_of,
                                "captured_at": _stamp(clock), "basis": "CAPTURED_BASELINE",
                                "base_quote": _price(market, row["ts_code"], as_of),
                                "base_factor": _factor(market, row["ts_code"], as_of, clock, tables),
                                "candidate_price": row.get("price")}
                    baselines.append({**baseline, "snapshot_hash": _hash(baseline)})
            rows = publication["candidates"]
            cohort, counts = evidence["cohort_key"], evidence["counts"]
            role = "PRIMARY" if primary_run_id == run_id else "SECONDARY"
            capture = {**evidence, "protocol_version": VERSION, "role": role, "primary_run_id": primary_run_id,
                       "captured_at": _stamp(clock), "capture_deadline": _stamp(deadline),
                       "candidate_hash": _hash(rows), "baseline_hash": _hash(baselines), "note": NOTE}
            side.execute("INSERT INTO forward_captures VALUES (?,?,?,?,?,?)",
                         (run_id, as_of, cohort, role, _stamp(clock), _json(capture)))
            for row in rows:
                pool, group = _group(row)
                side.execute("INSERT INTO forward_candidates VALUES (?,?,?,?,?)",
                             (run_id, row["ts_code"], pool, group, _json(row)))
            for baseline in baselines:
                side.execute("INSERT INTO forward_baselines VALUES (?,?,?)",
                             (run_id, baseline["ts_code"], _json(baseline)))
            _attempt(side, operation="capture", run_id=run_id, status="CAPTURED", clock=clock, counts=counts)
            side.commit()
            return {"status": "CAPTURED", "captured": True, **capture}
        except (ForwardObservationError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            side.rollback()
            result = _attempt(side, operation="capture", run_id=run_id, status="REJECTED", clock=clock,
                              captured=False, reason=getattr(exc, "code", "CAPTURE_FAILED"), message=str(exc))
            side.commit()
            return result


def forward_status(db_path: str | Path) -> dict:
    path = sidecar_path(db_path)
    empty = {"enabled": False, "protocol": None, "counts": {"captures": 0, "primary": 0, "secondary": 0,
             "candidates": 0, "outcomes": 0}, "last_attempt": None, "last_error": None,
             "uncaptured_runs": [], "missing_primary_runs": [], "note": NOTE}
    if not path.exists():
        return empty
    with _read(path) as side:
        protocol = _protocol(side)
        _require_market_binding(protocol, db_path)
        counts = {"captures": side.execute("SELECT COUNT(*) FROM forward_captures").fetchone()[0],
                  "primary": side.execute("SELECT COUNT(*) FROM forward_captures WHERE role='PRIMARY'").fetchone()[0],
                  "secondary": side.execute("SELECT COUNT(*) FROM forward_captures WHERE role='SECONDARY'").fetchone()[0],
                  "candidates": side.execute("SELECT COUNT(*) FROM forward_candidates").fetchone()[0],
                  "outcomes": side.execute("SELECT COUNT(*) FROM (SELECT 1 FROM forward_outcomes GROUP BY run_id,ts_code,horizon)").fetchone()[0]}
        last = side.execute("SELECT payload_json FROM forward_attempts ORDER BY id DESC LIMIT 1").fetchone()
        error = side.execute("SELECT payload_json FROM forward_attempts WHERE status IN ('REJECTED','FAILED') ORDER BY id DESC LIMIT 1").fetchone()
        captured_rows = side.execute("SELECT run_id,payload_json FROM forward_captures").fetchall()
        captured = {row[0] for row in captured_rows}
        missing_primary = {json.loads(row[1])["primary_run_id"] for row in captured_rows
                           if json.loads(row[1])["primary_run_id"] not in captured}
    uncaptured = []
    if protocol:
        # Also surface an absent capture when an I/O failure prevented attempt logging.
        with _read(Path(db_path)) as market:
            tables = {row[0] for row in market.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {"scan_runs", "scan_jobs"} <= tables:
                rows = market.execute("SELECT r.run_id,j.started_at FROM scan_runs r JOIN scan_jobs j ON j.task_id=r.task_id "
                                      "WHERE r.status='SUCCEEDED' ORDER BY r.created_at DESC LIMIT 100").fetchall()
                for row in rows:
                    try:
                        if row[0] not in captured and _parse_stamp(row[1]) > _parse_stamp(protocol["enabled_at"]):
                            uncaptured.append(row[0])
                    except ForwardObservationError:
                        continue
                from ab_screener.application.scan_publication import read_scan_publication
                for run_id in uncaptured:
                    try:
                        evidence = _publication_evidence(read_scan_publication(db_path, run_id, stage="qualified"), protocol, _clock())
                        deadline = _deadline(market, evidence["as_of"])
                        primary = _first_publication(db_path, market, evidence, protocol, _clock(), deadline)
                        if primary not in captured:
                            missing_primary.add(primary)
                    except (ForwardObservationError, ValueError, TypeError, KeyError):
                        continue
    return {**empty, "enabled": bool(protocol), "protocol": protocol, "counts": counts,
            "last_attempt": json.loads(last[0]) if last else None, "last_error": json.loads(error[0]) if error else None,
            "uncaptured_runs": uncaptured, "missing_primary_runs": sorted(missing_primary)}


def _positive(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _price(conn, code: str, day: str) -> dict:
    row = conn.execute("SELECT close,vol FROM daily WHERE ts_code=? AND trade_date=?", (code, day)).fetchone()
    observed = bool(row and _positive(row[1]))
    close = _positive(row[0]) if observed else None
    result = {"date": day, "close": close, "source": "daily.close",
              "status": "OBSERVED" if close else "INVALID_CLOSE" if observed else "NO_VOLUME" if row else "MISSING"}
    return {**result, "sha256": _hash(result)}


def _factor(conn, code: str, day: str, clock: datetime, tables: set[str]) -> dict:
    result = {"date": day, "factor": None, "table": "adj_factor_history", "source": None,
              "revision": None, "available_at": None, "status": "MISSING"}
    if "adj_factor_history" in tables:
        rows = conn.execute("SELECT revision,available_at,payload_json,content_hash,source FROM adj_factor_history "
                            "WHERE ts_code=? AND trade_date=? ORDER BY revision DESC", (code, day)).fetchall()
        if rows:
            result["status"] = "NOT_YET_AVAILABLE"
        for row in rows:
            # Offset-bearing timestamps are instants, not lexicographically sortable strings.
            try:
                available = _parse_stamp(row[1])
            except ForwardObservationError:
                result.update(status="INVALID_AVAILABLE_AT", revision=row[0], available_at=row[1])
                break
            if available > clock:
                continue
            result.update(revision=row[0], available_at=_stamp(available), source=row[4], source_hash=row[3])
            if type(row[0]) is not int or row[0] < 1:
                result["status"] = "INVALID_REVISION"
                break
            if not isinstance(row[4], str) or not row[4].strip():
                result["status"] = "INVALID_SOURCE"
                break
            try:
                payload = json.loads(row[2])
                if not isinstance(payload, dict):
                    raise TypeError("factor payload must be an object")
                # Ensure the canonical payload also contains no NaN/Infinity.
                _json(payload)
            except (ValueError, TypeError):
                result["status"] = "INVALID_PAYLOAD"
                break
            if row[3] != content_hash_for(payload):
                result["status"] = "INVALID_CONTENT_HASH"
                break
            value = payload.get("adj_factor")
            factor = _positive(value) if type(value) in (int, float) else None
            if factor is None:
                result["status"] = "INVALID_FACTOR"
                break
            result.update(factor=factor, status="AVAILABLE")
            break
    # Exact endpoint dates are required: never carry an old factor past its date.
    # A corrupted latest eligible revision blocks this endpoint; do not quietly
    # substitute an older revision and describe it as the current verified factor.
    return {**result, "sha256": _hash(result)}


def _returns(base_quote: dict, target_quote: dict, base_factor: dict, target_factor: dict) -> dict:
    result = {"raw_return": None, "adjusted_return": None, "status": "BASE_QUOTE_MISSING"}
    if base_quote["close"] is None:
        return result
    if target_quote["close"] is None:
        return {**result, "status": "TARGET_QUOTE_MISSING"}
    result.update(status="RAW_ONLY", raw_return=target_quote["close"] / base_quote["close"] - 1)
    if base_factor["factor"] and target_factor["factor"]:
        result.update(status="OBSERVED", adjusted_return=target_quote["close"] * target_factor["factor"]
                      / (base_quote["close"] * base_factor["factor"]) - 1)
    return result


def _revision_causes(result: dict, previous: dict | None) -> list[str]:
    if previous is None:
        causes = ["INITIAL_OBSERVATION"]
    else:
        causes = []
        if result["target_date"] != previous["target_date"]:
            causes.append("CALENDAR_TARGET_CHANGED")
        if result["status"] != previous["status"]:
            causes.append("OBSERVATION_STATUS_CHANGED")
        if result["target_quote"] != previous["target_quote"]:
            causes.append("TARGET_QUOTE_CHANGED")
        if result["target_factor"] != previous["target_factor"]:
            causes.append("TARGET_FACTOR_CHANGED")
    restated = result.get("restated")
    if restated and restated["differs_from_capture"] and (previous is None or restated != previous.get("restated")):
        causes.append("BASE_SOURCE_RESTATED")
    return causes or ["SOURCE_EVIDENCE_CHANGED"]


def refresh_forward_observations(db_path: str | Path, run_id: str | None = None, *, now: datetime | None = None) -> dict:
    path = sidecar_path(db_path)
    if not path.exists():
        return {"status": "DISABLED", "appended": 0}
    clock = _clock(now)
    appended = unchanged = 0
    with _write(path) as side, _read(Path(db_path)) as market:
        protocol = _protocol(side)
        if not protocol or protocol["market_database"] != str(Path(db_path).resolve()):
            raise ForwardObservationError("SIDECAR_MARKET_MISMATCH", "观察协议与行情库不匹配")
        _require_current_protocol(protocol)
        captures = side.execute("SELECT * FROM forward_captures" + (" WHERE run_id=?" if run_id else "")
                                + " ORDER BY captured_at", (run_id,) if run_id else ()).fetchall()
        if run_id and not captures:
            raise ForwardObservationError("CAPTURE_NOT_FOUND", "尚未捕获该扫描，不能计算观察结果")
        tables = {row[0] for row in market.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        side.execute("BEGIN IMMEDIATE")
        try:
            for capture in captures:
                targets = _calendar_targets(market, capture["as_of"])
                candidates = side.execute("SELECT * FROM forward_candidates WHERE run_id=? ORDER BY ts_code", (capture["run_id"],)).fetchall()
                captured_metadata = json.loads(capture["payload_json"])
                frozen_rows = [json.loads(row[0]) for row in side.execute(
                    "SELECT payload_json FROM forward_baselines WHERE run_id=? ORDER BY ts_code", (capture["run_id"],))]
                if len(frozen_rows) != len(candidates) or _hash(frozen_rows) != captured_metadata["baseline_hash"]:
                    raise ForwardObservationError("BASELINE_INTEGRITY_ERROR", "捕获基准快照数量或哈希不一致，禁止回读当前价代替")
                baselines = {row["ts_code"]: row for row in frozen_rows}
                for candidate in candidates:
                    code = candidate["ts_code"]
                    baseline = baselines[code]
                    if (baseline["run_id"] != capture["run_id"] or baseline["as_of"] != capture["as_of"]
                            or baseline["snapshot_hash"] != _hash({key: value for key, value in baseline.items() if key != "snapshot_hash"})):
                        raise ForwardObservationError("BASELINE_INTEGRITY_ERROR", "捕获基准身份或哈希不一致")
                    for horizon, target in targets.items():
                        result = {"run_id": capture["run_id"], "ts_code": code, "as_of": capture["as_of"],
                                  "role": capture["role"], "pool": candidate["pool"], "group": candidate["observation_group"],
                                  "horizon": horizon, "target_date": target, "metric": "price_observation", "metric_version": VERSION,
                                  "observation_basis": "CAPTURED_BASELINE", "baseline_snapshot_hash": baseline["snapshot_hash"],
                                  "baseline_captured_at": baseline["captured_at"],
                                  "raw_return": None, "adjusted_return": None, "status": "CALENDAR_UNVERIFIED",
                                  "base_quote": baseline["base_quote"], "target_quote": None,
                                  "base_factor": baseline["base_factor"], "target_factor": None, "restated": None}
                        if target:
                            mature = datetime.combine(datetime.strptime(target, "%Y%m%d").date(), time(16), _TZ)
                            result["status"] = "WAITING_HORIZON"
                            if clock >= mature:
                                target_quote = _price(market, code, target)
                                target_factor = _factor(market, code, target, clock, tables)
                                result.update(target_quote=target_quote, target_factor=target_factor,
                                              **_returns(baseline["base_quote"], target_quote, baseline["base_factor"], target_factor))
                                latest_base = _price(market, code, capture["as_of"])
                                latest_factor = _factor(market, code, capture["as_of"], clock, tables)
                                result["restated"] = {
                                    "basis": "LATEST_SOURCE_AT_REFRESH", "base_quote": latest_base, "base_factor": latest_factor,
                                    "differs_from_capture": latest_base != baseline["base_quote"] or latest_factor != baseline["base_factor"],
                                    **_returns(latest_base, target_quote, latest_factor, target_factor),
                                }
                        business_hash = _hash(result)
                        previous = side.execute("SELECT revision,business_hash,payload_json FROM forward_outcomes WHERE run_id=? AND ts_code=? AND horizon=? "
                                                "ORDER BY revision DESC LIMIT 1", (capture["run_id"], code, horizon)).fetchone()
                        if previous and previous[1] == business_hash:
                            unchanged += 1
                            continue
                        revision = previous[0] + 1 if previous else 1
                        lineage = {"previous_revision": previous[0] if previous else None,
                                   "previous_input_hash": previous[1] if previous else None,
                                   "baseline_snapshot_hash": baseline["snapshot_hash"], "baseline_captured_at": baseline["captured_at"],
                                   "causes": _revision_causes(result, json.loads(previous[2]) if previous else None)}
                        result.update(revision=revision, computed_at=_stamp(clock), input_hash=business_hash, lineage=lineage, note=NOTE)
                        side.execute("INSERT INTO forward_outcomes VALUES (?,?,?,?,?,?,?)",
                                     (capture["run_id"], code, horizon, revision, business_hash, _stamp(clock), _json(result)))
                        appended += 1
            summary = _attempt(side, operation="refresh", run_id=run_id, status="REFRESHED", clock=clock,
                               appended=appended, unchanged=unchanged, capture_count=len(captures))
            side.commit()
            return summary
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            side.rollback()
            result = _attempt(side, operation="refresh", run_id=run_id, status="FAILED", clock=clock,
                              reason=getattr(exc, "code", "REFRESH_FAILED"), message=str(exc), appended=0)
            side.commit()
            return result


def forward_history(db_path: str | Path, *, limit: int = 50, offset: int = 0) -> dict:
    path = sidecar_path(db_path)
    if not path.exists():
        return {"items": [], "total": 0, "note": NOTE}
    with _read(path) as side:
        _require_market_binding(_protocol(side), db_path)
        total = side.execute("SELECT COUNT(*) FROM forward_captures").fetchone()[0]
        rows = side.execute("SELECT payload_json FROM forward_captures ORDER BY captured_at DESC,run_id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"items": [json.loads(row[0]) for row in rows], "total": total, "note": NOTE}


def forward_results(db_path: str | Path, *, run_id: str | None = None, horizon: int | None = None,
                    limit: int = 100, offset: int = 0, all_revisions: bool = False) -> dict:
    path = sidecar_path(db_path)
    if not path.exists():
        return {"items": [], "total": 0, "note": NOTE}
    where, params = [], []
    if run_id:
        where.append("o.run_id=?")
        params.append(run_id)
    if horizon is not None:
        if horizon not in HORIZONS:
            raise ForwardObservationError("INVALID_HORIZON", "观察期限只能为1、5、10、20个交易日")
        where.append("o.horizon=?")
        params.append(horizon)
    if not all_revisions:
        where.append("o.revision=(SELECT MAX(v.revision) FROM forward_outcomes v WHERE v.run_id=o.run_id AND v.ts_code=o.ts_code AND v.horizon=o.horizon)")
    suffix = " WHERE " + " AND ".join(where) if where else ""
    with _read(path) as side:
        _require_market_binding(_protocol(side), db_path)
        total = side.execute("SELECT COUNT(*) FROM forward_outcomes o" + suffix, params).fetchone()[0]
        rows = side.execute("SELECT o.payload_json FROM forward_outcomes o" + suffix + " ORDER BY o.run_id,o.ts_code,o.horizon,o.revision DESC LIMIT ? OFFSET ?",
                            [*params, limit, offset]).fetchall()
    return {"items": [json.loads(row[0]) for row in rows], "total": total, "note": NOTE}
