"""Repair missing CSI300 point-in-time history from the configured provider.

This maintenance path never invents historical availability.  Existing
canonical rows are first preserved by the atomic market writer, while the
fresh provider observation is stamped with the actual ingestion time.  A
rerun is idempotent.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ab_screener.data.market_sync_writer import reconcile_market_partition

_TZ = ZoneInfo("Asia/Shanghai")


class BenchmarkPitSyncError(RuntimeError):
    """Fail-closed benchmark maintenance error."""


def missing_benchmark_pit_dates(
    db_path: str | Path,
    *,
    benchmark_code: str = "000300.SH",
    start: str | None = None,
    end: str | None = None,
) -> list[str]:
    """Return canonical benchmark dates that have no append-only history."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise BenchmarkPitSyncError(f"数据库不存在: {path}")
    clauses = ["d.ts_code=?", "h.ts_code IS NULL"]
    params: list[Any] = [benchmark_code]
    if start:
        clauses.append("d.trade_date>=?")
        params.append(_date(start))
    if end:
        clauses.append("d.trade_date<=?")
        params.append(_date(end))
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60) as conn:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not {"daily", "daily_history"} <= tables:
            raise BenchmarkPitSyncError("数据库缺少 daily/daily_history，拒绝维护")
        rows = conn.execute(
            "SELECT d.trade_date FROM daily d LEFT JOIN daily_history h"
            " ON h.ts_code=d.ts_code AND h.trade_date=d.trade_date WHERE "
            + " AND ".join(clauses)
            + " ORDER BY d.trade_date",
            params,
        ).fetchall()
    return [str(row[0]) for row in rows]


def sync_benchmark_pit_history(
    db_path: str | Path,
    provider: Any,
    *,
    benchmark_code: str = "000300.SH",
    start: str | None = None,
    end: str | None = None,
    available_at: str | datetime | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan or apply an idempotent provider-backed PIT repair."""
    path = Path(db_path).resolve()
    targets = missing_benchmark_pit_dates(
        path,
        benchmark_code=benchmark_code,
        start=start,
        end=end,
    )
    base = {
        "status": "NOOP" if not targets else ("PLANNED" if not apply else "RUNNING"),
        "db_path": str(path),
        "benchmark_code": benchmark_code,
        "target_start": targets[0] if targets else None,
        "target_end": targets[-1] if targets else None,
        "target_dates": len(targets),
        "applied_dates": 0,
        "recovered_revisions": 0,
        "provider_revisions": 0,
    }
    if not targets or not apply:
        return base
    if str(os.environ.get("LIVE_TRADING_ENABLED", "false")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise BenchmarkPitSyncError("LIVE_TRADING_ENABLED=true 时拒绝数据维护")

    frame = provider.index_daily(
        ts_code=benchmark_code,
        start_date=targets[0],
        end_date=targets[-1],
    )
    fetched = _validate_provider_frame(frame, benchmark_code=benchmark_code)
    by_date = {date: group.copy() for date, group in fetched.groupby("trade_date", sort=True)}
    missing_source = sorted(set(targets) - set(by_date))
    if missing_source:
        raise BenchmarkPitSyncError(f"供应商未覆盖全部目标日期: {missing_source[:5]}")

    observed_at = available_at or datetime.now(_TZ)
    recovered = 0
    appended = 0
    applied = 0
    for trade_date in targets:
        result = reconcile_market_partition(
            path,
            "daily",
            by_date[trade_date],
            trade_date=trade_date,
            available_at=observed_at,
            source="tushare_index_daily_pit_repair",
        )
        recovered += int(result["recovered_revisions"])
        appended += int(result["appended_revisions"])
        applied += 1

    remaining = missing_benchmark_pit_dates(
        path,
        benchmark_code=benchmark_code,
        start=targets[0],
        end=targets[-1],
    )
    if remaining:
        raise BenchmarkPitSyncError(f"维护后仍缺 PIT 日期: {remaining[:5]}")
    return {
        **base,
        "status": "COMPLETED",
        "available_at": str(observed_at),
        "applied_dates": applied,
        "recovered_revisions": recovered,
        "provider_revisions": appended,
    }


def _validate_provider_frame(frame: Any, *, benchmark_code: str) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        raise BenchmarkPitSyncError("供应商返回空的基准行情")
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "vol"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BenchmarkPitSyncError(f"供应商基准行情缺字段: {missing}")
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype(str).str.upper()
    result["trade_date"] = result["trade_date"].astype(str).str[:8]
    if set(result["ts_code"]) != {benchmark_code.upper()}:
        raise BenchmarkPitSyncError("供应商响应包含非目标基准代码")
    if result["trade_date"].duplicated().any():
        raise BenchmarkPitSyncError("供应商基准行情存在重复交易日")
    for column in ("open", "high", "low", "close", "vol"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[["open", "high", "low", "close", "vol"]].isna().any().any():
        raise BenchmarkPitSyncError("供应商基准行情包含非数值字段")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise BenchmarkPitSyncError("供应商基准行情包含非正价格")
    if (result["vol"] < 0).any():
        raise BenchmarkPitSyncError("供应商基准行情包含负成交量")
    invalid_ohlc = (
        (result["low"] > result["high"])
        | (result["open"] < result["low"])
        | (result["open"] > result["high"])
        | (result["close"] < result["low"])
        | (result["close"] > result["high"])
    )
    if invalid_ohlc.any():
        raise BenchmarkPitSyncError("供应商基准行情 OHLC 关系非法")
    return result.sort_values("trade_date").reset_index(drop=True)


def _date(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 8:
        raise BenchmarkPitSyncError(f"日期非法: {value!r}")
    return digits[:8]
