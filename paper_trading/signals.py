"""扫描结果到不可变交易信号快照及订单草稿的领域桥接。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trade_plan import build_trade_card

from .db import tx
from .errors import DomainError
from .orders import create_buy_draft

_TZ = ZoneInfo("Asia/Shanghai")
_POOL_RE = re.compile(r"\[池([AB])\|([^\]|]+)")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _pool_and_tier(reasons: str | None) -> tuple[str, str]:
    text = str(reasons or "")
    match = _POOL_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    if "theme_fill" in text:
        return "B", "theme_fill"
    if "relaxed" in text:
        return "B", "relaxed"
    return "A", "strict"


def _stable_hash(row: dict[str, Any], card: dict[str, Any], regime: str) -> str:
    payload = {
        "scan": {key: row.get(key) for key in sorted(row)},
        "card": card,
        "regime": regime,
        "strategy_version": "paper-signal-v1",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sync_signal_snapshots(
    db_path: str | Path,
    trade_date: str,
    *,
    regime: str = "neutral",
) -> dict[str, Any]:
    """把指定日 scan_result 固化为带时点和版本的 A/B 池信号快照。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            "SELECT * FROM scan_result WHERE trade_date=? ORDER BY ts_code",
            (trade_date,),
        ).fetchall()
        daily_meta = conn.execute(
            "SELECT MAX(available_at) FROM daily WHERE trade_date=?",
            (trade_date,),
        ).fetchone()

    fallback_available = (
        f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T15:30:00+08:00"
    )
    daily_available = str(daily_meta[0]) if daily_meta and daily_meta[0] else None
    now = _now()
    records: list[tuple[Any, ...]] = []
    a_count = 0
    for raw in rows:
        row = dict(raw)
        pool, tier = _pool_and_tier(row.get("reasons"))
        card = build_trade_card(
            price=row.get("price"),
            box_high=row.get("box_high"),
            box_low=row.get("box_low"),
            breakout_date=row.get("breakout_date"),
            tier=tier,
            regime=regime,
            score=row.get("total_score"),
        )
        tradeable = pool == "A" and bool(card["tradeable"])
        if pool == "A":
            a_count += 1
        available_at = str(row.get("created_at") or daily_available or fallback_available)
        effective_at = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T15:00:00+08:00"
        records.append((
            trade_date,
            str(row["ts_code"]),
            pool,
            row.get("total_score"),
            card.get("position_pct"),
            "paper-signal-v1",
            _stable_hash(row, card, regime),
            effective_at,
            available_at,
            now,
            "scan_result",
            1,
            1 if tradeable else 0,
        ))

    if records:
        with tx(db_path, immediate=True) as conn:
            conn.executemany(
                "INSERT INTO pt_signal_snapshot "
                "(trade_date, ts_code, pool, total_score, suggested_pos_pct, strategy_version,"
                " input_hash, effective_at, available_at, ingested_at, source, revision, tradeable)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(trade_date, ts_code, pool) DO UPDATE SET "
                "total_score=excluded.total_score,"
                " suggested_pos_pct=excluded.suggested_pos_pct,"
                " strategy_version=excluded.strategy_version,"
                " input_hash=excluded.input_hash, effective_at=excluded.effective_at,"
                " available_at=excluded.available_at, ingested_at=excluded.ingested_at,"
                " source=excluded.source, revision=pt_signal_snapshot.revision+1,"
                " tradeable=excluded.tradeable",
                records,
            )
    return {"trade_date": trade_date, "signals": len(records), "a_pool": a_count}


def generate_signal_drafts(
    db_path: str | Path,
    trade_date: str,
    *,
    today: str | None = None,
    regime: str = "neutral",
) -> dict[str, Any]:
    """为当日 tradeable A 池信号生成买入草稿；单票失败不会吞掉原因。"""
    db_path = Path(db_path)
    today = today or datetime.now(_TZ).strftime("%Y%m%d")
    if regime == "defense":
        return {"created": [], "rejected": [], "skipped": "MARKET_DEFENSE"}
    with tx(db_path, immediate=False) as conn:
        rows = conn.execute(
            "SELECT ts_code, total_score, suggested_pos_pct, input_hash "
            "FROM pt_signal_snapshot WHERE trade_date=? AND pool='A' AND tradeable=1 "
            "ORDER BY total_score DESC, ts_code",
            (trade_date,),
        ).fetchall()

    created: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for ts_code, score, pos_pct, input_hash in rows:
        try:
            created.append(create_buy_draft(
                db_path,
                ts_code=str(ts_code),
                trade_date=trade_date,
                suggested_pos_pct=float(pos_pct) if pos_pct is not None else None,
                total_score=float(score) if score is not None else None,
                input_hash=str(input_hash),
                today=today,
            ))
        except DomainError as exc:
            rejected.append({"ts_code": ts_code, "code": exc.code,
                             "message": exc.message})
    return {"created": created, "rejected": rejected}

