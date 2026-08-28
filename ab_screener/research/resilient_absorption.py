"""Preregistered research-only entry mechanisms for Breakout.

The production signal remains the frozen strict accumulation breakout.  A
research mechanism may only reject an already-causal base signal; it cannot
move the signal date or execution bar.  Every mechanism has an immutable
semantic fingerprint so all research stages can prove that they evaluated the
same economic hypothesis.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ab_screener.research.pit_reader import ResearchPitSnapshot

BASE_ENTRY_MECHANISM_ID = "BASE_STRICT_BREAKOUT_V1"
RESILIENT_ABSORPTION_ID = "RESILIENT_SUPPLY_ABSORPTION_V1"
BENCHMARK_CODE = "000300.SH"
CONTEXT_BENCHMARK_RETURN = "_pit_benchmark_return"
CONTEXT_BENCHMARK_CLOSE = "_pit_benchmark_close"
ENTRY_MECHANISM_KWARG = "entry_mechanism_id"


class ResearchEntryMechanismError(ValueError):
    """The requested research mechanism or its PIT context is invalid."""


_MECHANISMS: dict[str, dict[str, Any]] = {
    BASE_ENTRY_MECHANISM_ID: {
        "id": BASE_ENTRY_MECHANISM_ID,
        "version": "base-strict-breakout-v1.0.0",
        "research_only": False,
        "economic_hypothesis": "No additional filter beyond the frozen strict breakout.",
        "conditions": [],
    },
    RESILIENT_ABSORPTION_ID: {
        "id": RESILIENT_ABSORPTION_ID,
        "version": "resilient-supply-absorption-v1.0.0",
        "research_only": True,
        "benchmark_code": BENCHMARK_CODE,
        "economic_hypothesis": (
            "A genuine accumulation box absorbs systematic selling, retains positive "
            "volume-weighted price pressure, and closes an eventual breakout in the "
            "upper half of its intraday range."
        ),
        "conditions": [
            {
                "id": "relative_resilience_on_benchmark_down_days",
                "minimum_observations": 4,
                "rule": "median(stock_close_return - benchmark_close_return) > 0",
            },
            {
                "id": "positive_volume_weighted_box_pressure",
                "minimum_observations": 8,
                "rule": "mean(stock_close_return * volume / median_box_volume) > 0",
            },
            {
                "id": "breakout_upper_half_close",
                "rule": "(2*close-high-low)/(high-low) > 0",
            },
        ],
        "missing_data_policy": "fail_closed_no_imputation",
        "timing": "box_and_breakout_close_only_then_next_tradable_open",
        "parameter_search": "none",
    },
}


def registered_entry_mechanism_ids() -> tuple[str, ...]:
    return tuple(sorted(_MECHANISMS))


def entry_mechanism_snapshot(mechanism_id: str) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(_MECHANISMS[mechanism_id], ensure_ascii=False))
    except KeyError as exc:
        raise ResearchEntryMechanismError(
            f"未知研究入场机制: {mechanism_id}（已注册: {registered_entry_mechanism_ids()}）"
        ) from exc


def entry_mechanism_semantic_hash(mechanism_id: str) -> str:
    snapshot = entry_mechanism_snapshot(mechanism_id)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def entry_mechanism_identity(mechanism_id: str) -> dict[str, Any]:
    snapshot = entry_mechanism_snapshot(mechanism_id)
    return {
        "id": mechanism_id,
        "version": snapshot["version"],
        "semantic_hash": entry_mechanism_semantic_hash(mechanism_id),
        "research_only": bool(snapshot["research_only"]),
        "benchmark_code": snapshot.get("benchmark_code"),
        "parameter_search": snapshot.get("parameter_search", "none"),
    }


def resolve_requested_entry_mechanism(requested: Any) -> dict[str, Any]:
    """Resolve and verify a request-bound identity; omission means frozen base behavior."""
    if requested is None:
        return entry_mechanism_identity(BASE_ENTRY_MECHANISM_ID)
    if not isinstance(requested, Mapping):
        raise ResearchEntryMechanismError("entry_mechanism 必须是版本化身份对象")
    mechanism_id = str(requested.get("id") or "")
    expected = entry_mechanism_identity(mechanism_id)
    if dict(requested) != expected:
        raise ResearchEntryMechanismError("请求绑定的入场机制与当前语义指纹不一致")
    return expected


def signal_kwargs_for_entry_mechanism(mechanism_id: str) -> dict[str, str]:
    entry_mechanism_snapshot(mechanism_id)
    return {ENTRY_MECHANISM_KWARG: mechanism_id}


def split_signal_kwargs(
    signal_kwargs: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Separate research mechanism metadata from strict detector parameters."""
    detector_kwargs = dict(signal_kwargs or {})
    mechanism_id = str(
        detector_kwargs.pop(ENTRY_MECHANISM_KWARG, BASE_ENTRY_MECHANISM_ID)
        or BASE_ENTRY_MECHANISM_ID
    )
    entry_mechanism_snapshot(mechanism_id)
    return detector_kwargs, mechanism_id


def prepare_signal_market_context(
    daily: pd.DataFrame,
    *,
    research_snapshot: ResearchPitSnapshot | None,
    start: str,
    end: str,
    signal_kwargs: Mapping[str, Any] | None,
) -> pd.DataFrame:
    """Attach the frozen benchmark context required by a research mechanism."""
    _detector_kwargs, mechanism_id = split_signal_kwargs(signal_kwargs)
    if mechanism_id == BASE_ENTRY_MECHANISM_ID:
        return daily
    if mechanism_id != RESILIENT_ABSORPTION_ID:
        raise ResearchEntryMechanismError(f"研究机制未实现: {mechanism_id}")
    if research_snapshot is None:
        raise ResearchEntryMechanismError("韧性吸收机制必须绑定冻结 PIT 快照")
    if research_snapshot.benchmark_code != BENCHMARK_CODE:
        raise ResearchEntryMechanismError(
            f"韧性吸收机制要求 PIT 基准 {BENCHMARK_CODE}，实际为 {research_snapshot.benchmark_code}"
        )
    benchmark = research_snapshot.load_benchmark(start=start, end=end)
    return attach_frozen_benchmark_context(daily, benchmark)


def attach_frozen_benchmark_context(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> pd.DataFrame:
    """Merge one immutable benchmark return series by trade date, rejecting ambiguity."""
    required = {"trade_date", "close"}
    if not required.issubset(benchmark.columns):
        raise ResearchEntryMechanismError("PIT 基准缺少 trade_date/close")
    bench = benchmark.loc[:, ["trade_date", "close"]].copy()
    bench["trade_date"] = bench["trade_date"].astype(str).str[:8]
    if bench["trade_date"].duplicated().any():
        raise ResearchEntryMechanismError("PIT 基准同一交易日存在重复记录")
    bench[CONTEXT_BENCHMARK_CLOSE] = pd.to_numeric(bench["close"], errors="coerce")
    if (
        bench[CONTEXT_BENCHMARK_CLOSE].isna().any()
        or (bench[CONTEXT_BENCHMARK_CLOSE] <= 0).any()
    ):
        raise ResearchEntryMechanismError("PIT 基准收盘价缺失或非正")
    bench = bench.sort_values("trade_date").reset_index(drop=True)
    bench[CONTEXT_BENCHMARK_RETURN] = bench[CONTEXT_BENCHMARK_CLOSE].pct_change(fill_method=None)
    context = bench.loc[
        :, ["trade_date", CONTEXT_BENCHMARK_CLOSE, CONTEXT_BENCHMARK_RETURN]
    ]
    result = daily.copy()
    result["trade_date"] = result["trade_date"].astype(str).str[:8]
    return result.merge(context, on="trade_date", how="left", validate="many_to_one")


def evaluate_entry_mechanism(
    mechanism_id: str,
    bars: pd.DataFrame,
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    if mechanism_id == BASE_ENTRY_MECHANISM_ID:
        return {
            "passed": True,
            "mechanism": entry_mechanism_identity(mechanism_id),
            "checks": [],
        }
    if mechanism_id != RESILIENT_ABSORPTION_ID:
        raise ResearchEntryMechanismError(f"研究机制未实现: {mechanism_id}")
    return _evaluate_resilient_absorption(bars, signal)


def _evaluate_resilient_absorption(
    bars: pd.DataFrame,
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    identity = entry_mechanism_identity(RESILIENT_ABSORPTION_ID)
    base = {"passed": False, "mechanism": identity, "checks": []}
    required = {
        "trade_date",
        "close",
        "high",
        "low",
        "vol",
        CONTEXT_BENCHMARK_RETURN,
    }
    if bars is None or bars.empty or not required.issubset(bars.columns):
        return {**base, "reason": "MECHANISM_CONTEXT_INCOMPLETE"}
    breakout_date = _date(signal.get("breakout_date"))
    box_days = _positive_int(signal.get("box_days"))
    if not breakout_date or box_days is None:
        return {**base, "reason": "BASE_SIGNAL_GEOMETRY_INCOMPLETE"}

    frame = bars.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).map(_date)
    frame = frame[frame["trade_date"] <= breakout_date].sort_values("trade_date").reset_index(drop=True)
    matches = frame.index[frame["trade_date"] == breakout_date].tolist()
    if len(matches) != 1:
        return {**base, "reason": "BREAKOUT_BAR_NOT_UNIQUE"}
    breakout_index = int(matches[0])
    box_start = breakout_index - box_days
    if box_start < 0:
        return {**base, "reason": "BOX_HISTORY_INCOMPLETE"}

    for column in ("close", "high", "low", "vol", CONTEXT_BENCHMARK_RETURN):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["_stock_return"] = frame["close"].pct_change(fill_method=None)
    box = frame.iloc[box_start:breakout_index].copy()
    if len(box) != box_days:
        return {**base, "reason": "BOX_HISTORY_INCOMPLETE"}

    valid = box[
        np.isfinite(box["_stock_return"])
        & np.isfinite(box[CONTEXT_BENCHMARK_RETURN])
        & np.isfinite(box["vol"])
        & (box["vol"] > 0)
    ].copy()
    median_volume = float(valid["vol"].median()) if not valid.empty else float("nan")
    valid_count = len(valid)
    pressure = (
        float((valid["_stock_return"] * valid["vol"] / median_volume).mean())
        if valid_count >= 8 and np.isfinite(median_volume) and median_volume > 0
        else None
    )
    pressure_pass = pressure is not None and np.isfinite(pressure) and pressure > 0

    market_down = valid[valid[CONTEXT_BENCHMARK_RETURN] < 0].copy()
    down_count = len(market_down)
    resilience = (
        float(
            (market_down["_stock_return"] - market_down[CONTEXT_BENCHMARK_RETURN]).median()
        )
        if down_count >= 4
        else None
    )
    resilience_pass = resilience is not None and np.isfinite(resilience) and resilience > 0

    breakout = frame.iloc[breakout_index]
    high = float(breakout["high"]) if np.isfinite(breakout["high"]) else float("nan")
    low = float(breakout["low"]) if np.isfinite(breakout["low"]) else float("nan")
    close = float(breakout["close"]) if np.isfinite(breakout["close"]) else float("nan")
    clv = (
        float((2.0 * close - high - low) / (high - low))
        if np.isfinite(high) and np.isfinite(low) and np.isfinite(close) and high > low
        else None
    )
    clv_pass = clv is not None and np.isfinite(clv) and clv > 0

    checks = [
        {
            "id": "relative_resilience_on_benchmark_down_days",
            "passed": bool(resilience_pass),
            "actual": resilience,
            "observations": down_count,
            "threshold": "> 0 with at least 4 observations",
        },
        {
            "id": "positive_volume_weighted_box_pressure",
            "passed": bool(pressure_pass),
            "actual": pressure,
            "observations": valid_count,
            "median_volume": median_volume if np.isfinite(median_volume) else None,
            "threshold": "> 0 with at least 8 observations",
        },
        {
            "id": "breakout_upper_half_close",
            "passed": bool(clv_pass),
            "actual": clv,
            "threshold": "> 0",
        },
    ]
    passed = all(bool(check["passed"]) for check in checks)
    failed = [str(check["id"]) for check in checks if not check["passed"]]
    return {
        "passed": passed,
        "mechanism": identity,
        "box_start": str(box.iloc[0]["trade_date"]) if not box.empty else None,
        "box_end": str(box.iloc[-1]["trade_date"]) if not box.empty else None,
        "breakout_date": breakout_date,
        "checks": checks,
        "reason": "OK" if passed else "MECHANISM_CHECK_FAILED",
        "failed_checks": failed,
    }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""
