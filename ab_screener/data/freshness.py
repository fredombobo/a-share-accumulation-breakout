"""Daily quote timeliness and per-stock moneyflow window completeness."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ab_screener.data.trading_calendar import calendar_window, date_key

_TZ = ZoneInfo("Asia/Shanghai")
FUND_FLOW_COLUMNS = ("buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount")
FUND_FLOW_BASIS_NOTE = "沿用评分字段：net_mf_amount 优先，否则大单及特大单买卖净差；资金净额与比率须结合字段来源解读"


def _dataset_freshness(db_path, expected: str) -> dict[str, dict[str, Any]]:
    """Read global watermarks without creating or migrating a database."""
    result = {
        name: {"latest_date": "", "expected_as_of": expected, "is_current": False,
               "status": "DATASET_UNAVAILABLE"}
        for name in ("daily_basic", "moneyflow")
    }
    if not db_path or not Path(db_path).is_file():
        return result
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn, conn:
            conn.execute("PRAGMA query_only=ON")
            for table, item in result.items():
                try:
                    # Names come only from the fixed allowlist above.
                    row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
                    latest = date_key(row[0]) if row and row[0] else ""
                    current = bool(expected and latest and latest >= expected)
                    item.update(latest_date=latest, is_current=current,
                                status="CURRENT" if current else "DATASET_MISSING" if not latest
                                else "CALENDAR_UNVERIFIED" if not expected else "DATASET_BEHIND_EXPECTED")
                except (sqlite3.Error, ValueError):
                    item["status"] = "DATASET_UNAVAILABLE"
    except (sqlite3.Error, OSError):
        pass
    return result


def assess_data_freshness(
    as_of: str,
    today: str | None = None,
    trade_dates: list[str] | None = None,
    store=None,
    now: datetime | None = None,
    *,
    reference_now: datetime | None = None,
    historical: bool = False,
    required_moneyflow_days: int = 5,
) -> dict[str, Any]:
    """Only the independent trade_cal table can certify a completed session.

    trade_dates is retained for old callers, but observed quote dates never
    determine expected_as_of. For replay pass reference_now or historical=True;
    historical=True without a clock evaluates as_of at 16:00 Shanghai time.
    """
    clock = reference_now or now
    if historical and clock is None:
        clock = datetime.strptime(date_key(as_of), "%Y%m%d").replace(hour=16, tzinfo=_TZ)
    clock = clock or datetime.now(_TZ)
    clock = clock.replace(tzinfo=_TZ) if clock.tzinfo is None else clock.astimezone(_TZ)
    today_key = date_key(today) if today else clock.strftime("%Y%m%d")
    try:
        observed = date_key(as_of) if as_of else ""
    except ValueError:
        observed = ""
    path = getattr(store, "db_path", None)
    calendar = calendar_window(path, today=today_key, as_of=observed or None,
                               before_today=clock.hour < 16, required_days=required_moneyflow_days)
    expected = calendar["expected_as_of"] if calendar["verified"] else ""
    reasons = []
    if not calendar["verified"]:
        reasons.append(calendar["status"])
    if not observed:
        reasons.append("DATA_MISSING" if not as_of else "INVALID_AS_OF")
    elif expected and observed > expected:
        reasons.append("AS_OF_AFTER_EXPECTED")
    elif expected and observed not in calendar["open_dates"]:
        reasons.append("AS_OF_NOT_OPEN_SESSION")
    elif expected and observed < expected:
        reasons.append("DATA_BEHIND_EXPECTED")
    datasets = _dataset_freshness(path, expected)
    if expected:
        for name, item in datasets.items():
            if not item["is_current"]:
                reasons.append(f"{name.upper()}_{item['status'].removeprefix('DATASET_')}")
    stale = sum(observed < d <= expected for d in calendar["open_dates"]) if observed and expected else 999
    current = bool(observed and expected and observed == expected and not reasons)
    label = "新鲜" if current else ("无数据" if not observed else "日历待核对" if not calendar["verified"] else "数据未齐" if observed == expected else "偏旧" if stale == 1 else "日期异常" if observed > expected else "过期")
    return {
        "as_of": observed, "today": today_key, "stale_days": stale,
        "is_stale": not current, "is_current": current, "label": label, "unit": "trading",
        "expected_as_of": expected,
        "stale_label": f"滞后 {stale} 个交易日" if expected and observed < expected else label,
        "calendar_verified": calendar["verified"], "calendar_status": calendar["status"],
        "calendar_source": calendar["source"], "calendar_reason": calendar["reason"],
        "calendar_missing_dates": calendar["missing_dates"],
        "required_moneyflow_dates": calendar["required_dates"] if calendar["verified"] else [],
        "dataset_freshness": datasets,
        "can_publish_a": current, "blocking_reasons": reasons,
        "reference_now": clock.isoformat(timespec="seconds"), "historical": historical,
    }


def moneyflow_window_status(
    mf_rows: pd.DataFrame | None,
    *,
    expected_dates: Sequence[str] | None,
    expected_as_of: str,
    required_days: int = 5,
) -> dict[str, Any]:
    """Check distinct, finite observations on the specified independent window.

    Missing rows are unknown, not zero and not automatically a sync fault (a
    listing or suspension can also cause gaps). The scoring formula is unchanged.
    """
    required_days = max(1, int(required_days))
    try:
        reference = date_key(expected_as_of)
        dates = sorted({date_key(d) for d in (expected_dates or []) if date_key(d) <= reference})[-required_days:]
    except ValueError:
        reference, dates = "", []
    result: dict[str, Any] = {
        "complete": False, "status": "WINDOW_REFERENCE_MISSING", "expected_as_of": reference,
        "required_days": required_days, "expected_dates": dates, "observed_dates": [],
        "missing_dates": dates[:], "invalid_dates": [], "duplicate_dates": [],
        "observed_days": 0, "basis": "unavailable", "denominator_basis": "unavailable", "basis_note": FUND_FLOW_BASIS_NOTE,
        "reason": "缺少独立交易日历资金窗口",
    }
    if len(dates) != required_days or not reference or dates[-1] != reference:
        return result
    if mf_rows is None or mf_rows.empty or "trade_date" not in mf_rows:
        result.update(status="MONEYFLOW_MISSING", reason=f"资金窗口缺少 {required_days}/{required_days} 日数据")
        return result
    data = mf_rows.copy()
    def normalize(value):
        try:
            return date_key(value)
        except ValueError:
            return ""
    data["_date"] = data["trade_date"].map(normalize)
    data = data.loc[data["_date"].isin(dates)]
    observed = sorted(set(data["_date"]))
    duplicate = sorted(set(data.loc[data["_date"].duplicated(keep=False), "_date"]))
    missing = sorted(set(dates) - set(observed))
    if "net_mf_amount" in data:
        values = pd.to_numeric(data["net_mf_amount"], errors="coerce")
        valid = np.isfinite(values)
        basis = "net_mf_amount"
    elif all(c in data for c in FUND_FLOW_COLUMNS):
        values = data[list(FUND_FLOW_COLUMNS)].apply(pd.to_numeric, errors="coerce")
        valid = np.isfinite(values).all(axis=1)
        basis = "large_plus_extra_large_net"
    else:
        valid = pd.Series(False, index=data.index)
        basis = "unavailable"
    denominator_columns = ["amount"] if "amount" in data else list(FUND_FLOW_COLUMNS)
    denominator_basis = "amount" if "amount" in data else "large_plus_extra_large_buy_and_sell"
    if all(column in data for column in denominator_columns):
        denominator = data[denominator_columns].apply(pd.to_numeric, errors="coerce")
        valid = valid & np.isfinite(denominator).all(axis=1) & (denominator >= 0).all(axis=1)
    else:
        valid = pd.Series(False, index=data.index)
        denominator_basis = "unavailable"
    invalid = sorted(set(data.loc[~valid, "_date"]))
    complete = not missing and not duplicate and not invalid
    status = "COMPLETE" if complete else "MONEYFLOW_WINDOW_INCOMPLETE" if missing else "MONEYFLOW_DUPLICATE_DATES" if duplicate else "MONEYFLOW_INVALID_VALUES"
    reason = "资金观察窗口完整" if complete else f"资金窗口不完整：有效 {len(set(observed) - set(invalid) - set(duplicate))}/{required_days} 日，缺日 {len(missing)}，无效 {len(invalid)}，重复 {len(duplicate)}；仅作观察"
    result.update(complete=complete, status=status, observed_dates=observed, missing_dates=missing,
                  invalid_dates=invalid, duplicate_dates=duplicate, observed_days=len(set(observed) - set(invalid) - set(duplicate)),
                  basis=basis, denominator_basis=denominator_basis, reason=reason)
    return result


def moneyflow_rows_for_window(mf_rows: pd.DataFrame | None, window: dict[str, Any]) -> pd.DataFrame:
    """Select only valid unique window rows before the unchanged scoring formula."""
    if mf_rows is None or mf_rows.empty or "trade_date" not in mf_rows:
        return pd.DataFrame()
    data = mf_rows.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.replace("-", "", regex=False)
    usable = set(window["expected_dates"]) - set(window["invalid_dates"]) - set(window["duplicate_dates"])
    return data.loc[data["trade_date"].isin(usable)].copy()
