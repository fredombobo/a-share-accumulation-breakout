"""Causal post-breakout supply dry-up confirmation.

The mechanism starts from an unchanged strict accumulation-breakout signal at
``t0``.  It accepts the signal only after the immediately following stock bar
closes above the frozen box resistance on lower volume and in the upper half of
its daily range.  The caller must additionally verify that ``t0`` and ``t1`` are
adjacent exchange sessions before an order can be simulated.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

POST_BREAKOUT_SUPPLY_DRY_UP_ID = "POST_BREAKOUT_SUPPLY_DRY_UP_V1"


def detect_post_breakout_supply_dry_up(
    bars: pd.DataFrame,
    detector_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect one strict ``t0`` breakout and confirm it causally at ``t1``."""
    from ab_screener.signals import detect_accumulation_breakout

    required = {"high", "low", "close", "vol"}
    if (
        bars is None
        or len(bars) < 2
        or not required.issubset(bars.columns)
        or not ({"trade_date", "date"} & set(bars.columns))
    ):
        return _rejected("MECHANISM_CONTEXT_INCOMPLETE")
    frame = _normalized_frame(bars)
    if len(frame) < 2:
        return _rejected("MECHANISM_CONTEXT_INCOMPLETE")

    confirmation_date = _date(frame.iloc[-1]["trade_date"])
    history = frame.iloc[:-1].copy()
    initial = detect_accumulation_breakout(history, **dict(detector_kwargs or {}))
    if not initial.get("is_breakout"):
        return _rejected("BASE_STRICT_BREAKOUT_NOT_CONFIRMED")

    initial_date = _date(initial.get("breakout_date"))
    previous_bar_date = _date(history.iloc[-1]["trade_date"])
    if not initial_date or initial_date != previous_bar_date:
        return _rejected("BASE_BREAKOUT_NOT_ON_PREVIOUS_STOCK_BAR")

    candidate = {
        **initial,
        "is_breakout": False,
        "initial_breakout_date": initial_date,
        "confirmation_date": confirmation_date,
        # The decision is made after t1 close; execution is therefore t2 open.
        "breakout_date": confirmation_date,
    }
    evidence = evaluate_post_breakout_supply_dry_up(frame, candidate)
    candidate["entry_mechanism_evidence"] = evidence
    if not evidence.get("passed"):
        candidate["reasons"] = [str(evidence.get("reason") or "MECHANISM_CHECK_FAILED")]
        return candidate
    candidate["is_breakout"] = True
    candidate["reasons"] = [
        *[str(value) for value in initial.get("reasons") or [] if value],
        "突破次日缩量站稳箱顶且收于日内上半区",
    ]
    return candidate


def evaluate_post_breakout_supply_dry_up(
    bars: pd.DataFrame,
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute mechanism evidence using no bar later than confirmation close."""
    initial_date = _date(signal.get("initial_breakout_date"))
    confirmation_date = _date(signal.get("confirmation_date") or signal.get("breakout_date"))
    base: dict[str, Any] = {
        "passed": False,
        "initial_breakout_date": initial_date or None,
        "confirmation_date": confirmation_date or None,
        "checks": [],
    }
    required = {"high", "low", "close", "vol"}
    if (
        bars is None
        or bars.empty
        or not required.issubset(bars.columns)
        or not ({"trade_date", "date"} & set(bars.columns))
        or not initial_date
        or not confirmation_date
    ):
        return _finalize({**base, "reason": "MECHANISM_CONTEXT_INCOMPLETE"})

    frame = _normalized_frame(bars)
    frame = frame[frame["trade_date"] <= confirmation_date].reset_index(drop=True)
    initial_rows = frame.index[frame["trade_date"] == initial_date].tolist()
    confirmation_rows = frame.index[frame["trade_date"] == confirmation_date].tolist()
    if len(initial_rows) != 1 or len(confirmation_rows) != 1:
        return _finalize({**base, "reason": "CONFIRMATION_BARS_NOT_UNIQUE"})
    initial_index = int(initial_rows[0])
    confirmation_index = int(confirmation_rows[0])
    adjacent_stock_bar = confirmation_index == initial_index + 1

    initial = frame.iloc[initial_index]
    confirmation = frame.iloc[confirmation_index]
    initial_volume = _finite_positive(initial.get("vol"))
    confirmation_volume = _finite_positive(confirmation.get("vol"))
    high = _finite(confirmation.get("high"))
    low = _finite(confirmation.get("low"))
    close = _finite(confirmation.get("close"))
    box_high = _finite(signal.get("box_high"))
    clv = (
        (2.0 * close - high - low) / (high - low)
        if close is not None and high is not None and low is not None and high > low
        else None
    )
    price_accepted = close is not None and box_high is not None and close > box_high
    volume_dry_up = (
        initial_volume is not None
        and confirmation_volume is not None
        and confirmation_volume < initial_volume
    )
    upper_half_close = clv is not None and math.isfinite(clv) and clv > 0
    checks = [
        {
            "id": "next_stock_bar_confirmation",
            "passed": adjacent_stock_bar,
            "actual": confirmation_index - initial_index,
            "threshold": "exactly 1 stock bar",
        },
        {
            "id": "close_accepted_above_frozen_box_high",
            "passed": price_accepted,
            "actual": close,
            "box_high": box_high,
            "threshold": "close(t1) > box_high(t0)",
        },
        {
            "id": "post_breakout_volume_dry_up",
            "passed": volume_dry_up,
            "actual": (
                confirmation_volume / initial_volume
                if initial_volume is not None and confirmation_volume is not None
                else None
            ),
            "initial_volume": initial_volume,
            "confirmation_volume": confirmation_volume,
            "threshold": "0 < volume(t1) / volume(t0) < 1",
        },
        {
            "id": "confirmation_upper_half_close",
            "passed": upper_half_close,
            "actual": clv,
            "threshold": "CLV(t1) > 0",
        },
    ]
    passed = all(bool(item["passed"]) for item in checks)
    failed = [str(item["id"]) for item in checks if not item["passed"]]
    return _finalize(
        {
            **base,
            "passed": passed,
            "checks": checks,
            "failed_checks": failed,
            "reason": "OK" if passed else "MECHANISM_CHECK_FAILED",
        }
    )


def add_exchange_session_check(
    evidence: Mapping[str, Any],
    *,
    initial_breakout_date: str,
    confirmation_date: str,
    exchange_session_gap: int | None,
) -> dict[str, Any]:
    """Attach the exchange-calendar adjacency proof and refresh the audit hash."""
    result = {key: value for key, value in dict(evidence).items() if key != "evidence_sha256"}
    checks = [dict(item) for item in result.get("checks") or []]
    passed = exchange_session_gap == 1
    checks.append(
        {
            "id": "next_exchange_session_confirmation",
            "passed": passed,
            "actual": exchange_session_gap,
            "initial_breakout_date": initial_breakout_date,
            "confirmation_date": confirmation_date,
            "threshold": "exactly 1 exchange session",
        }
    )
    result["checks"] = checks
    result["passed"] = bool(result.get("passed")) and passed
    failed = [str(item["id"]) for item in checks if not item.get("passed")]
    result["failed_checks"] = failed
    result["reason"] = "OK" if result["passed"] else "MECHANISM_CHECK_FAILED"
    return _finalize(result)


def _normalized_frame(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    if "trade_date" not in frame.columns and "date" in frame.columns:
        frame["trade_date"] = frame["date"]
    frame["trade_date"] = frame["trade_date"].astype(str).map(_date)
    frame = frame[frame["trade_date"] != ""].sort_values("trade_date").reset_index(drop=True)
    if "date" not in frame.columns:
        frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d").dt.strftime(
            "%Y-%m-%d"
        )
    for column in ("high", "low", "close", "vol"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in result.items() if key != "evidence_sha256"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        **payload,
        "evidence_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _rejected(reason: str) -> dict[str, Any]:
    return {
        "is_breakout": False,
        "breakout_date": None,
        "initial_breakout_date": None,
        "confirmation_date": None,
        "reasons": [reason],
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _finite_positive(value: Any) -> float | None:
    result = _finite(value)
    return result if result is not None and result > 0 else None


def _date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""
