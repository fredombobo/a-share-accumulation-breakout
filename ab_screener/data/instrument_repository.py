"""instrument 仓库：宇宙规则投影 upsert + PIT 历史 + as-of 宇宙（fail-closed）。

契约（implementation P1.2）：
- `instrument_universe_rules` 是可变投影（白名单字段 upsert，非账本；
  与 paper_trading.instrument_rules 成本规则表区分）；
  每次变更同时追加 `instrument_lifecycle_history`（PIT 审计）。
- `universe_asof(as_of)` 只返回该时点有效且类型允许的代码；
  注册表为空（尚未回填）→ 抛错（fail-closed，绝不退化为全市场默认）。
- 指数/ETF/基金/债券/北交所/孤儿代码不进入首版个股宇宙。
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.instrument import Instrument, classify_security

_TZ = ZoneInfo("Asia/Shanghai")
ALLOWED_TYPES = ("stock", "index", "etf", "fund", "bond", "bse", "other")

_RULES_TABLE = "instrument_universe_rules"
_HISTORY_TABLE = "instrument_lifecycle_history"


class InstrumentRegistryError(RuntimeError):
    """instrument 注册表缺失/为空：fail-closed 信号。"""


class InstrumentMissingError(ValueError):
    """指定 ts_code 无有效规则：回测/订单不得兜底。"""


def _checksum(rule: Instrument) -> str:
    return hashlib.sha256(canonical_json(rule.to_payload()).encode("utf-8")).hexdigest()[:16]


def upsert_instrument(conn: sqlite3.Connection, rule: Instrument, available_at: Any = None) -> None:
    """写入/更新规则：投影 upsert + PIT 历史追加。available_at 缺省用当前时刻。"""
    if rule.security_type not in ALLOWED_TYPES:
        raise ValueError(f"非法 security_type: {rule.security_type}")
    from ab_screener.domain.data_point import normalize_ts

    available = normalize_ts(available_at or datetime.now(_TZ))
    payload = rule.to_payload()
    checksum = _checksum(rule)
    conn.execute(
        f"INSERT INTO {_RULES_TABLE} (ts_code, name, exchange, security_type, list_date,"
        " delist_date, source, updated_at, checksum)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(ts_code) DO UPDATE SET name=excluded.name, exchange=excluded.exchange,"
        " security_type=excluded.security_type, list_date=excluded.list_date,"
        " delist_date=excluded.delist_date, source=excluded.source,"
        " updated_at=excluded.updated_at, checksum=excluded.checksum",
        (rule.ts_code, rule.name, rule.exchange, rule.security_type, rule.list_date,
         rule.delist_date, rule.source, datetime.now(_TZ).isoformat(timespec="seconds"), checksum),
    )
    rev_row = conn.execute(
        f"SELECT COALESCE(MAX(revision),0) FROM {_HISTORY_TABLE} WHERE ts_code=?",
        (rule.ts_code,),
    ).fetchone()
    conn.execute(
        f"INSERT INTO {_HISTORY_TABLE} (ts_code, revision, available_at, source,"
        " content_hash, payload_json) VALUES (?,?,?,?,?,?)",
        (rule.ts_code, int(rev_row[0] or 0) + 1, available, rule.source, checksum,
         canonical_json(payload)),
    )
    conn.commit()


def get_instrument(conn: sqlite3.Connection, ts_code: str) -> Instrument | None:
    row = conn.execute(
        f"SELECT ts_code, name, exchange, security_type, list_date, delist_date, source"
        f" FROM {_RULES_TABLE} WHERE ts_code=?",
        (ts_code,),
    ).fetchone()
    if row is None:
        return None
    return Instrument(
        ts_code=row[0], name=row[1], exchange=row[2], security_type=row[3],
        list_date=row[4], delist_date=row[5], source=row[6],
    )


def universe_asof(
    conn: sqlite3.Connection,
    as_of: str,
    security_types: Iterable[str] = ("stock",),
) -> list[str]:
    """as_of 时点有效且类型允许的代码（升序）。注册表为空 → fail-closed。"""
    types = tuple(security_types)
    total = conn.execute(f"SELECT COUNT(*) FROM {_RULES_TABLE}").fetchone()[0]
    if total == 0:
        raise InstrumentRegistryError("instrument 注册表为空：先回填 instrument 规则（fail-closed）")
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"SELECT ts_code, list_date, delist_date FROM {_RULES_TABLE}"
        f" WHERE security_type IN ({placeholders})",
        types,
    ).fetchall()
    out = []
    for ts_code, list_date, delist_date in rows:
        if not list_date or as_of < list_date:
            continue
        if delist_date and as_of >= delist_date:
            continue
        out.append(ts_code)
    return sorted(out)


def require_instrument(
    conn: sqlite3.Connection,
    ts_code: str,
    as_of: str,
    security_types: Iterable[str] = ("stock",),
) -> Instrument:
    """as_of 时刻该代码必须有有效规则，否则抛错（不兜底）。"""
    rule = get_instrument(conn, ts_code)
    if rule is None:
        raise InstrumentMissingError(
            f"缺少 instrument 规则: {ts_code}（禁止全市场默认值兜底）"
        )
    if rule.security_type not in tuple(security_types):
        raise InstrumentMissingError(
            f"{ts_code} 类型 {rule.security_type} 不在允许集合 {tuple(security_types)} 内"
        )
    if not rule.is_active_at(as_of):
        raise InstrumentMissingError(
            f"{ts_code} 在 {as_of} 不在生命周期内（list={rule.list_date}"
            f" delist={rule.delist_date or '-'}）"
        )
    return rule


def load_from_csv(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    """从 universe_lifecycle.csv 装载规则（测试/离线用途），返回行数。"""
    import csv

    rows = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            sec_type = (rec.get("security_type") or "").strip().lower()
            if not sec_type:
                sec_type = classify_security(rec["ts_code"])
            rule = Instrument(
                ts_code=rec["ts_code"].strip(),
                name=rec.get("name", "").strip(),
                exchange=rec.get("exchange", "").strip(),
                security_type=sec_type,
                list_date=rec["list_date"].strip(),
                delist_date=(rec.get("delist_date") or "").strip() or None,
            )
            upsert_instrument(conn, rule)
            rows += 1
    return rows
