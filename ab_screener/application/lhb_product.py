"""可运行的龙虎榜盘后产品流水线（仅显式数据库副本、research-only）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.application.lhb_daily import clamp_signal_status, emit_quality_alert, run_lhb_day
from ab_screener.application.lhb_profiles import build_profile, facts_to_events
from ab_screener.application.lhb_signal_engine import run_signal
from ab_screener.application.lhb_transform import transform_day
from ab_screener.application.pit_backfill import assert_copy_database
from ab_screener.data.adapters.lhb_sources import FetchResult, TushareLhbAdapter
from ab_screener.data.lhb_repository import (
    persist_normalized_day,
    save_profile_snapshot,
    save_signal_observation,
)
from ab_screener.data.seat_repository import save_hypothesis
from ab_screener.domain.data_point import canonical_json, content_hash_for
from ab_screener.domain.lhb_signal import SignalInput
from ab_screener.domain.seat_identity import hypotheses_from_hm_list
from ab_screener.features.lhb_features import LhbSeatFact, compute_seat_features
from ab_screener.operations.lhb_alerts import create_alert
from ab_screener.research.seat_style import classify_seat_style

_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _combined_status(results: dict[str, FetchResult]) -> str:
    statuses = {item.source_status for item in results.values()}
    if "FETCH_FAILED" in statuses:
        return "FETCH_FAILED"
    if "NOT_PUBLISHED" in statuses:
        return "NOT_PUBLISHED"
    if "DEGRADED" in statuses or ("COMPLETE" in statuses and "VALID_EMPTY" in statuses):
        return "DEGRADED"
    if statuses == {"VALID_EMPTY"}:
        return "VALID_EMPTY"
    return "COMPLETE"


def _persist_fetch(conn: sqlite3.Connection, result: FetchResult) -> None:
    from ab_screener.application.lhb_ingest import persist_fetch

    persist_fetch(conn, result)


def _load_facts(conn: sqlite3.Connection, *, trade_date: str, as_of: str) -> list[LhbSeatFact]:
    rows = conn.execute(
        "WITH ev AS (SELECT e.*,ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY revision DESC) rn"
        " FROM lhb_event e WHERE disclose_date<=? AND available_at<=?), tr AS ("
        " SELECT t.*,ROW_NUMBER() OVER (PARTITION BY event_id,seat_raw ORDER BY revision DESC) rn"
        " FROM lhb_seat_trade t WHERE available_at<=?)"
        " SELECT tr.seat_id,tr.seat_raw,ev.ts_code,ev.disclose_date,ev.available_at,tr.revision,"
        " tr.buy_amount_fen,tr.sell_amount_fen,tr.net_amount_fen,ev.event_id"
        " FROM ev JOIN tr ON tr.event_id=ev.event_id AND tr.rn=1 WHERE ev.rn=1"
        " ORDER BY ev.disclose_date,ev.event_id,tr.seat_raw",
        (trade_date, as_of, as_of),
    ).fetchall()
    facts: list[LhbSeatFact] = []
    for seat_id, seat_raw, ts_code, day, available, revision, buy, sell, net, event_id in rows:
        sid = str(seat_id or seat_raw)
        actor = conn.execute(
            "WITH ranked AS (SELECT actor_id,confidence,revision,ROW_NUMBER() OVER ("
            " PARTITION BY actor_id,valid_from ORDER BY revision DESC) rn"
            " FROM seat_actor_hypothesis WHERE seat_id=? AND valid_from<=?"
            " AND (valid_to IS NULL OR valid_to>?) AND available_at<=?)"
            " SELECT actor_id FROM ranked WHERE rn=1 ORDER BY confidence DESC,actor_id LIMIT 1",
            (sid, day, day, as_of),
        ).fetchone()
        basic = conn.execute(
            "SELECT d.turnover_rate,d.circ_mv,s.industry FROM daily_basic d"
            " LEFT JOIN stock_basic s ON s.ts_code=d.ts_code"
            " WHERE d.ts_code=? AND d.trade_date=? LIMIT 1",
            (ts_code, day),
        ).fetchone()
        turnover = float(basic[0]) if basic and basic[0] is not None else None
        # Tushare daily_basic.circ_mv 单位万元；进入领域层转为元。
        float_mv_yuan = float(basic[1]) * 10_000.0 if basic and basic[1] is not None else None
        industry = str(basic[2]) if basic and basic[2] else None
        facts.append(
            LhbSeatFact(
                seat_id=sid,
                actor_id=str(actor[0]) if actor else sid,
                ts_code=str(ts_code),
                trade_date=str(day),
                available_at=str(available),
                revision=int(revision),
                buy_fen=int(buy),
                sell_fen=int(sell),
                net_fen=int(net),
                industry=industry,
                float_mv_yuan=float_mv_yuan,
                turnover=turnover,
                event_id=str(event_id),
            )
        )
    return facts


def _save_profiles(conn: sqlite3.Connection, *, trade_date: str, as_of: str) -> dict[str, int]:
    facts = _load_facts(conn, trade_date=trade_date, as_of=as_of)
    counts = {"seat": 0, "actor": 0, "stock": 0, "board": 0}
    subjects = {
        "seat": sorted({fact.seat_id for fact in facts}),
        "actor": sorted({fact.actor_id for fact in facts}),
        "stock": sorted({fact.ts_code for fact in facts}),
        "board": sorted({fact.industry or "UNK" for fact in facts}),
    }
    for subject_type, subject_ids in subjects.items():
        events = facts_to_events(facts, as_of=as_of, subject_type=subject_type)  # type: ignore[arg-type]
        for subject_id in subject_ids:
            profile = build_profile(
                events,
                subject_type=subject_type,  # type: ignore[arg-type]
                subject_id=subject_id,
                window_days=60,
                as_of_date=trade_date,
            )
            profile["model_version"] = "lhb-profiles-v1"
            if subject_type == "seat":
                feature = compute_seat_features(
                    facts,
                    seat_id=subject_id,
                    as_of=as_of,
                    as_of_date=trade_date,
                    window_days=60,
                )
                profile["rolling_features"] = feature
                profile["style"] = classify_seat_style(feature)
            save_profile_snapshot(conn, profile, as_of=as_of)
            counts[subject_type] += 1
    conn.commit()
    return counts


def _future_calendar(conn: sqlite3.Connection, trade_date: str) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT cal_date FROM trade_cal WHERE cal_date>? AND is_open=1 ORDER BY cal_date LIMIT 10",
        (trade_date,),
    ).fetchall()
    return (trade_date, *[str(row[0]) for row in rows])


def _save_signals(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    as_of: str,
    source_status: str,
) -> int:
    # 历史补抓的 available_at 晚于披露日，不能假装当时已知并倒灌信号。
    if as_of[:10].replace("-", "") != trade_date:
        return 0
    facts = [fact for fact in _load_facts(conn, trade_date=trade_date, as_of=as_of) if fact.trade_date == trade_date]
    calendar = _future_calendar(conn, trade_date)
    if len(calendar) < 2:
        return 0
    written = 0
    for ts_code in sorted({fact.ts_code for fact in facts}):
        rows = [fact for fact in facts if fact.ts_code == ts_code]
        buy = sum(fact.buy_fen for fact in rows) / 100.0
        sell = sum(fact.sell_fen for fact in rows) / 100.0
        net = buy - sell
        event_amount = conn.execute(
            "SELECT json_extract(payload_json,'$.amount') FROM lhb_event"
            " WHERE ts_code=? AND disclose_date=? AND available_at<=?"
            " ORDER BY revision DESC LIMIT 1",
            (ts_code, trade_date, as_of),
        ).fetchone()
        amount = float(event_amount[0] or 0.0) if event_amount else 0.0
        adv = conn.execute(
            "SELECT AVG(amount)*1000.0 FROM (SELECT amount FROM daily WHERE ts_code=?"
            " AND trade_date<=? ORDER BY trade_date DESC LIMIT 20)",
            (ts_code, trade_date),
        ).fetchone()
        adv20 = float(adv[0] or 0.0) if adv else 0.0
        turnover_row = conn.execute(
            "SELECT turnover_rate FROM daily_basic WHERE ts_code=? AND trade_date=?",
            (ts_code, trade_date),
        ).fetchone()
        turnover = float(turnover_row[0] or 0.0) if turnover_row else 0.0
        actors = {fact.actor_id for fact in rows}
        data_complete = source_status == "COMPLETE" and amount > 0 and adv20 > 0
        inp = SignalInput(
            ts_code=ts_code,
            disclose_date=trade_date,
            disclose_at=as_of,
            net_yuan=net,
            amount_yuan=amount,
            adv20_yuan=adv20,
            purity=abs(net) / max(buy + sell, 1.0),
            independent_actors=len(actors),
            identity_confidence=0.5,
            identity_grade="B",
            turnover=turnover,
            data_complete=data_complete,
            next_bar_unfillable=False,
            next_bar_suspended=False,
            liquid=adv20 >= 20_000_000.0,
            crowded=turnover > 25.0,
            severe_abnormal=False,
            calendar=calendar,
            data_version=f"lhb:{trade_date}",
            identity_version=f"seat-map:{trade_date}",
            feature_snapshot={"fact_hash": content_hash_for([fact.__dict__ for fact in rows])},
        )
        signal = run_signal(inp)
        signal["status"] = clamp_signal_status(str(signal["status"]), source_status)
        signal["available_at"] = as_of
        save_signal_observation(conn, signal)
        written += 1
    conn.commit()
    return written


def run_lhb_product_day(
    db_path: str | Path,
    trade_date: str,
    *,
    holder: str = "lhb-product",
    pro: Any | None = None,
    published: bool = True,
    now_iso: Any | None = None,
) -> dict[str, Any]:
    """在显式副本执行完整日终；所有通知保持 dry-run。"""
    db = assert_copy_database(db_path, maintenance_authorized=False)
    state: dict[str, Any] = {"fetch": {}, "status": "FETCH_FAILED", "as_of": _now()}
    adapter = TushareLhbAdapter(pro=pro, now_iso=now_iso)

    def ingest(*, trade_date: str) -> None:
        fetched = {
            name: adapter.fetch(name, trade_date, published=published)
            for name in ("top_list", "top_inst", "hm_list")
        }
        with sqlite3.connect(str(db)) as conn:
            for result in fetched.values():
                _persist_fetch(conn, result)
            if fetched["hm_list"].source_status == "COMPLETE":
                for hyp in hypotheses_from_hm_list(fetched["hm_list"].rows, list_date=trade_date):
                    save_hypothesis(
                        conn,
                        hyp,
                        available_at=fetched["hm_list"].available_at,
                        source="tushare_hm_list",
                        confidence=0.65,
                    )
        state["fetch"] = fetched
        state["status"] = _combined_status({k: v for k, v in fetched.items() if k != "hm_list"})
        state["as_of"] = max(item.available_at for item in fetched.values())

    def reconcile(*, trade_date: str) -> None:
        # 官方源未获授权时不伪造对账；状态由 quality/report 明示。
        state["official_reconciliation"] = "NOT_AUTHORIZED"

    def transform(*, trade_date: str) -> None:
        fetched: dict[str, FetchResult] = state["fetch"]
        if state["status"] in {"FETCH_FAILED", "NOT_PUBLISHED"}:
            state["normalized"] = {"events": 0, "trades": 0, "ranks": 0, "seats": 0}
            return
        normalized = transform_day(
            disclose_date=trade_date,
            top_list_rows=list(fetched["top_list"].rows),
            top_inst_rows=list(fetched["top_inst"].rows),
            available_at=state["as_of"],
            source_status=state["status"],
        )
        with sqlite3.connect(str(db)) as conn:
            state["normalized"] = persist_normalized_day(
                conn, normalized, available_at=state["as_of"]
            )

    def map_seats(*, trade_date: str) -> None:
        state["mapping"] = "PIT_APPLIED_DURING_TRANSFORM"

    def features(*, trade_date: str) -> None:
        with sqlite3.connect(str(db)) as conn:
            state["profiles"] = _save_profiles(
                conn, trade_date=trade_date, as_of=state["as_of"]
            )

    def signals(*, trade_date: str) -> None:
        with sqlite3.connect(str(db)) as conn:
            state["signals"] = _save_signals(
                conn,
                trade_date=trade_date,
                as_of=state["as_of"],
                source_status=state["status"],
            )

    def report(*, trade_date: str) -> None:
        report_dir = db.parent / "lhb_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "trade_date": trade_date,
            "generated_at": _now(),
            "db_path": str(db),
            "source_status": state["status"],
            "research_only": True,
            "may_generate_orders": False,
            "official_reconciliation": state.get("official_reconciliation"),
            "normalized": state.get("normalized"),
            "profiles": state.get("profiles"),
            "signals": state.get("signals"),
        }
        (report_dir / f"{trade_date}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        state["report"] = payload

    def alerts(*, trade_date: str) -> None:
        with sqlite3.connect(str(db)) as conn:
            quality = emit_quality_alert(
                conn, trade_date=trade_date, source_status=state["status"], dry_run=True
            )
            large = conn.execute(
                "WITH event_latest AS ("
                " SELECT event_id,ts_code,disclose_date,"
                " ROW_NUMBER() OVER (PARTITION BY event_id"
                " ORDER BY revision DESC,available_at DESC) rn"
                " FROM lhb_event), trade_latest AS ("
                " SELECT event_id,seat_raw,net_amount_fen,"
                " ROW_NUMBER() OVER (PARTITION BY event_id,seat_raw"
                " ORDER BY revision DESC,available_at DESC) rn"
                " FROM lhb_seat_trade)"
                " SELECT e.ts_code,SUM(t.net_amount_fen)"
                " FROM trade_latest t JOIN event_latest e ON e.event_id=t.event_id"
                " WHERE e.rn=1 AND t.rn=1 AND e.disclose_date=? GROUP BY e.ts_code"
                " HAVING SUM(net_amount_fen)>=?",
                (trade_date, 50_000_000 * 100),
            ).fetchall()
            created = []
            for ts_code, net_fen in large:
                created.append(
                    create_alert(
                        conn,
                        alert_type="LARGE_NET_BUY",
                        trade_date=trade_date,
                        payload={"ts_code": ts_code, "net_yuan": int(net_fen) / 100.0},
                        severity="INFO",
                        dry_run=True,
                    )
                )
            state["alerts"] = {"quality": quality, "large_net_buy": len(created), "dry_run": True}

    fns = {
        "lhb_ingest": ingest,
        "lhb_reconcile": reconcile,
        "lhb_transform": transform,
        "lhb_map": map_seats,
        "lhb_features": features,
        "lhb_signals": signals,
        "lhb_report": report,
        "lhb_alerts": alerts,
    }
    input_hash = content_hash_for(
        {"trade_date": trade_date, "pipeline": "lhb-product-v1", "published": published}
    )
    result = run_lhb_day(
        str(db), trade_date, holder=holder, input_hash=input_hash, fns=fns, source_status=state["status"]
    )
    result["source_status"] = state["status"]
    result["confirmed_blocked"] = state["status"] in {
        "FETCH_FAILED",
        "DEGRADED",
        "NOT_PUBLISHED",
        "VALID_EMPTY",
    }
    result["product"] = {k: v for k, v in state.items() if k != "fetch"}
    result["research_only"] = True
    result["may_generate_orders"] = False
    return result


def result_json(result: dict[str, Any]) -> str:
    return canonical_json(result)
