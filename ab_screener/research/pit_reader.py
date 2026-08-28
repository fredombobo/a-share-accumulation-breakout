"""Frozen point-in-time market snapshot for authoritative Breakout research.

The research knowledge cutoff is preregistered before a run starts.  Every
business key is resolved to the greatest revision whose ``available_at`` is no
later than that cutoff.  Historical backfills retain their real ingestion
availability; the simulation timeline (signal day -> next open) remains a
separate concern.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ab_screener.domain.data_point import content_hash_for, normalize_ts

PIT_READER_VERSION = "research-pit-reader-v2.0.0"
_SQL_CHUNK_SIZE = 500
_REQUIRED_PRICE_FIELDS = ("open", "high", "low", "close")


class ResearchPitError(RuntimeError):
    """Fail-closed PIT snapshot or lineage error."""


@dataclass(frozen=True)
class ResearchPitSnapshot:
    """One immutable-by-contract knowledge snapshot shared by all run stages."""

    decision_at: str
    data_start: str
    data_end: str
    universe: tuple[str, ...]
    universe_sha256: str
    dataset_fingerprint: str
    daily: pd.DataFrame
    version: str = PIT_READER_VERSION
    benchmark_code: str | None = None
    benchmark_sha256: str | None = None
    benchmark_daily: pd.DataFrame = dataclass_field(default_factory=pd.DataFrame, repr=False)

    def load_daily(
        self,
        *,
        ts_codes: list[str] | tuple[str, ...] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        requested_start = _date(start or self.data_start)
        requested_end = _date(end or self.data_end)
        if requested_start < self.data_start or requested_end > self.data_end:
            raise ResearchPitError(
                f"请求窗口 {requested_start}~{requested_end} 超出冻结快照 {self.data_start}~{self.data_end}"
            )
        if requested_start > requested_end:
            raise ResearchPitError("PIT 行情窗口起止日期倒置")
        selected = self.daily[
            (self.daily["trade_date"] >= requested_start) & (self.daily["trade_date"] <= requested_end)
        ]
        if ts_codes is not None:
            requested_codes = {str(code).upper() for code in ts_codes}
            unknown = sorted(requested_codes - set(self.universe))
            if unknown:
                raise ResearchPitError(f"请求包含冻结宇宙之外的代码: {unknown[:5]}")
            selected = selected[selected["ts_code"].isin(requested_codes)]
        return selected.copy().reset_index(drop=True)

    def distinct_dates(self, *, start: str | None = None, end: str | None = None) -> list[str]:
        frame = self.load_daily(start=start, end=end)
        return sorted(frame["trade_date"].astype(str).unique().tolist())

    def load_benchmark(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Read the frozen PIT benchmark without treating it as a stock."""
        requested_start = _date(start or self.data_start)
        requested_end = _date(end or self.data_end)
        if requested_start < self.data_start or requested_end > self.data_end:
            raise ResearchPitError(
                f"请求基准窗口 {requested_start}~{requested_end} 超出冻结快照 "
                f"{self.data_start}~{self.data_end}"
            )
        if requested_start > requested_end:
            raise ResearchPitError("PIT 基准行情窗口起止日期倒置")
        if not self.benchmark_code or self.benchmark_daily.empty:
            raise ResearchPitError("冻结快照未绑定基准行情")
        selected = self.benchmark_daily[
            (self.benchmark_daily["trade_date"] >= requested_start)
            & (self.benchmark_daily["trade_date"] <= requested_end)
        ]
        return selected.copy().reset_index(drop=True)

    def identity(self) -> dict[str, Any]:
        result = {
            "version": self.version,
            "decision_at": self.decision_at,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "universe_size": len(self.universe),
            "universe_sha256": self.universe_sha256,
            "dataset_fingerprint": self.dataset_fingerprint,
        }
        if self.benchmark_code:
            result.update(
                {
                    "benchmark_code": self.benchmark_code,
                    "benchmark_sha256": self.benchmark_sha256,
                    "benchmark_rows": len(self.benchmark_daily),
                }
            )
        return result


def latest_research_cutoff(db_path: str | Path) -> str:
    """Return the latest immutable knowledge boundary present in PIT tables."""
    with _connect(db_path) as conn:
        _require_tables(conn)
        values = [
            row[0]
            for row in (
                conn.execute("SELECT MAX(available_at) FROM daily_history").fetchone(),
                conn.execute("SELECT MAX(available_at) FROM instrument_lifecycle_history").fetchone(),
            )
            if row and row[0]
        ]
    if not values:
        raise ResearchPitError("PIT 历史表为空，不能启动可信研究")
    return max(normalize_ts(value) for value in values)


def build_research_pit_snapshot(
    db_path: str | Path,
    *,
    study_start: str,
    study_end: str,
    max_codes: int,
    decision_at: str | None = None,
    history_days: int = 365,
    benchmark_code: str | None = None,
) -> ResearchPitSnapshot:
    """Freeze universe and daily revisions for one preregistered research run."""
    start = _date(study_start)
    end = _date(study_end)
    if start > end:
        raise ResearchPitError("研究窗口起止日期倒置")
    if max_codes <= 0:
        raise ResearchPitError("max_codes 必须为正整数")
    cutoff = normalize_ts(decision_at or latest_research_cutoff(db_path))
    data_start = (pd.to_datetime(start) - pd.Timedelta(days=history_days)).strftime("%Y%m%d")

    benchmark = pd.DataFrame()
    resolved_benchmark = str(benchmark_code or "").strip().upper() or None
    with _connect(db_path) as conn:
        _require_tables(conn)
        universe_evidence = _load_universe_evidence(
            conn,
            study_start=start,
            study_end=end,
            decision_at=cutoff,
        )
        universe_evidence = universe_evidence[:max_codes]
        if not universe_evidence:
            raise ResearchPitError("PIT 生命周期在研究窗口内未形成股票宇宙")
        codes = tuple(row["ts_code"] for row in universe_evidence)
        universe_sha = _universe_hash(universe_evidence, cutoff, start, end)
        daily = _load_daily_asof(
            conn,
            codes=codes,
            start=data_start,
            end=end,
            decision_at=cutoff,
        )
        if resolved_benchmark:
            benchmark = _load_daily_asof(
                conn,
                codes=(resolved_benchmark,),
                start=data_start,
                end=end,
                decision_at=cutoff,
            )

    if daily.empty:
        raise ResearchPitError("冻结宇宙在研究窗口内没有 PIT 日线")
    if resolved_benchmark and benchmark.empty:
        raise ResearchPitError(f"冻结快照缺少基准 PIT 日线: {resolved_benchmark}")
    benchmark_sha = _daily_hash(benchmark) if resolved_benchmark else None
    dataset_hash = _dataset_hash(
        daily,
        cutoff=cutoff,
        data_start=data_start,
        data_end=end,
        universe_sha256=universe_sha,
        benchmark_sha256=benchmark_sha,
    )
    return ResearchPitSnapshot(
        decision_at=cutoff,
        data_start=data_start,
        data_end=end,
        universe=codes,
        universe_sha256=universe_sha,
        dataset_fingerprint=dataset_hash,
        daily=daily,
        benchmark_code=resolved_benchmark,
        benchmark_sha256=benchmark_sha,
        benchmark_daily=benchmark,
    )


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = str(Path(db_path).resolve())
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def _require_tables(conn: sqlite3.Connection) -> None:
    required = {
        "daily",
        "daily_history",
        "instrument_lifecycle_history",
    }
    found = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    missing = sorted(required - found)
    if missing:
        raise ResearchPitError(f"缺少 PIT 研究表: {missing}")


def _load_universe_evidence(
    conn: sqlite3.Connection,
    *,
    study_start: str,
    study_end: str,
    decision_at: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "WITH ranked AS ("
        " SELECT ts_code,revision,available_at,source,content_hash,payload_json,"
        " ROW_NUMBER() OVER (PARTITION BY ts_code"
        " ORDER BY revision DESC, available_at DESC) AS rn"
        " FROM instrument_lifecycle_history WHERE available_at <= ?"
        ") SELECT ts_code,revision,available_at,source,content_hash,payload_json"
        " FROM ranked WHERE rn=1 ORDER BY ts_code",
        (decision_at,),
    ).fetchall()
    evidence: list[dict[str, Any]] = []
    for ts_code, revision, available_at, source, content_hash, payload_json in rows:
        payload = _payload(payload_json, content_hash, f"instrument:{ts_code}")
        code = str(ts_code).upper()
        if str(payload.get("ts_code") or code).upper() != code:
            raise ResearchPitError(f"instrument 业务键与 payload 不一致: {code}")
        security_type = str(payload.get("security_type") or "").lower()
        list_date = _date(payload.get("list_date"))
        delist_raw = payload.get("delist_date")
        delist_date = _date(delist_raw) if delist_raw else ""
        if security_type != "stock" or not _is_mainland_stock(code):
            continue
        if not list_date:
            raise ResearchPitError(f"instrument 缺 list_date: {code}")
        if list_date > study_end or (delist_date and delist_date <= study_start):
            continue
        evidence.append(
            {
                "ts_code": code,
                "revision": int(revision),
                "available_at": normalize_ts(available_at),
                "source": _source(source, f"instrument:{code}"),
                "content_hash": str(content_hash),
                "list_date": list_date,
                "delist_date": delist_date or None,
            }
        )
    return evidence


def _load_daily_asof(
    conn: sqlite3.Connection,
    *,
    codes: tuple[str, ...],
    start: str,
    end: str,
    decision_at: str,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for code_chunk in _chunks(codes, _SQL_CHUNK_SIZE):
        placeholders = ",".join("?" for _ in code_chunk)
        missing = conn.execute(
            "SELECT d.ts_code,d.trade_date FROM daily d"
            f" WHERE d.ts_code IN ({placeholders})"
            " AND d.trade_date>=? AND d.trade_date<=?"
            " AND (d.available_at IS NULL OR d.available_at<=?)"
            " AND NOT EXISTS (SELECT 1 FROM daily_history h"
            " WHERE h.ts_code=d.ts_code AND h.trade_date=d.trade_date"
            " AND h.available_at<=?) LIMIT 6",
            (*code_chunk, start, end, decision_at, decision_at),
        ).fetchall()
        if missing:
            samples = [f"{row[0]}:{row[1]}" for row in missing]
            raise ResearchPitError(f"投影存在但 PIT 截止点缺日线: {samples}")

        rows = conn.execute(
            "WITH ranked AS ("
            " SELECT ts_code,trade_date,revision,available_at,source,content_hash,payload_json,"
            " ROW_NUMBER() OVER (PARTITION BY ts_code,trade_date"
            " ORDER BY revision DESC, available_at DESC) AS rn"
            " FROM daily_history"
            f" WHERE ts_code IN ({placeholders})"
            " AND trade_date>=? AND trade_date<=? AND available_at<=?"
            ") SELECT ts_code,trade_date,revision,available_at,source,content_hash,payload_json"
            " FROM ranked WHERE rn=1 ORDER BY ts_code,trade_date",
            (*code_chunk, start, end, decision_at),
        ).fetchall()
        for ts_code, trade_date, revision, available_at, source, content_hash, payload_json in rows:
            key = f"daily:{ts_code}:{trade_date}"
            payload = _payload(payload_json, content_hash, key)
            record = _daily_record(
                ts_code=str(ts_code).upper(),
                trade_date=_date(trade_date),
                revision=revision,
                available_at=available_at,
                source=source,
                content_hash=content_hash,
                payload=payload,
            )
            records.append(record)
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    duplicate = frame.duplicated(["ts_code", "trade_date"], keep=False)
    if duplicate.any():
        samples = frame.loc[duplicate, ["ts_code", "trade_date"]].head(5).to_dict("records")
        raise ResearchPitError(f"PIT 日线选择后仍有重复业务键: {samples}")
    return frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _daily_record(
    *,
    ts_code: str,
    trade_date: str,
    revision: Any,
    available_at: Any,
    source: Any,
    content_hash: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    volume = payload.get("vol")
    if volume is None or float(volume) < 0:
        raise ResearchPitError(f"PIT 日线成交量非法: {ts_code}:{trade_date}")
    values: dict[str, float] = {}
    for field in _REQUIRED_PRICE_FIELDS:
        value = payload.get(field)
        if value is None:
            raise ResearchPitError(f"PIT 日线字段非法: {ts_code}:{trade_date}:{field}")
        values[field] = float(value)
    if values["close"] <= 0:
        raise ResearchPitError(f"PIT 日线收盘价非法: {ts_code}:{trade_date}")
    suspended_zero_quote = (
        float(volume) == 0 and values["open"] == 0 and values["high"] == 0 and values["low"] == 0
    )
    if not suspended_zero_quote:
        low = values["low"]
        high = values["high"]
        if (
            min(values[field] for field in ("open", "high", "low")) <= 0
            or low > high
            or not low <= values["open"] <= high
            or not low <= values["close"] <= high
        ):
            raise ResearchPitError(f"PIT 日线 OHLC 关系非法: {ts_code}:{trade_date}")
    result = dict(payload)
    result.update(
        {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "revision": int(revision),
            "available_at": normalize_ts(available_at),
            "source": _source(source, f"daily:{ts_code}:{trade_date}"),
            "content_hash": str(content_hash),
        }
    )
    result.setdefault("pre_close", None)
    result.setdefault("amount", 0)
    return result


def _payload(payload_json: Any, expected_hash: Any, key: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(payload_json))
    except (TypeError, ValueError) as exc:
        raise ResearchPitError(f"PIT payload 无法解析: {key}") from exc
    if not isinstance(payload, dict):
        raise ResearchPitError(f"PIT payload 不是对象: {key}")
    actual_hash = content_hash_for(payload)
    if not expected_hash or actual_hash != str(expected_hash):
        raise ResearchPitError(
            f"PIT content_hash 不一致: {key} expected={expected_hash} actual={actual_hash}"
        )
    return payload


def _source(value: Any, key: str) -> str:
    source = str(value or "").strip()
    if not source:
        raise ResearchPitError(f"PIT source 缺失: {key}")
    return source


def _universe_hash(
    evidence: list[dict[str, Any]],
    cutoff: str,
    start: str,
    end: str,
) -> str:
    digest = hashlib.sha256(f"{PIT_READER_VERSION}|{cutoff}|{start}|{end}\n".encode())
    for row in evidence:
        digest.update(
            "|".join(
                str(row.get(key) or "")
                for key in (
                    "ts_code",
                    "revision",
                    "available_at",
                    "source",
                    "content_hash",
                    "list_date",
                    "delist_date",
                )
            ).encode("utf-8")
            + b"\n"
        )
    return digest.hexdigest()


def _dataset_hash(
    daily: pd.DataFrame,
    *,
    cutoff: str,
    data_start: str,
    data_end: str,
    universe_sha256: str,
    benchmark_sha256: str | None = None,
) -> str:
    digest = hashlib.sha256(
        (
            f"{PIT_READER_VERSION}|{cutoff}|{data_start}|{data_end}|"
            f"{universe_sha256}|{benchmark_sha256 or ''}\n"
        ).encode()
    )
    for row in daily[
        [
            "ts_code",
            "trade_date",
            "revision",
            "available_at",
            "source",
            "content_hash",
        ]
    ].itertuples(index=False, name=None):
        digest.update("|".join(str(value) for value in row).encode("utf-8") + b"\n")
    digest.update(f"rows={len(daily)}".encode("ascii"))
    return digest.hexdigest()[:16]


def _daily_hash(daily: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    if daily.empty:
        return digest.hexdigest()
    for row in daily[
        [
            "ts_code",
            "trade_date",
            "revision",
            "available_at",
            "source",
            "content_hash",
        ]
    ].itertuples(index=False, name=None):
        digest.update("|".join(str(value) for value in row).encode("utf-8") + b"\n")
    digest.update(f"rows={len(daily)}".encode("ascii"))
    return digest.hexdigest()


def _chunks(values: tuple[str, ...], size: int) -> Iterator[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _is_mainland_stock(code: str) -> bool:
    return code.endswith((".SH", ".SZ")) and not code.startswith(("4", "8", "92"))


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 8:
        raise ResearchPitError(f"日期字段非法: {value!r}")
    return digits[:8]
