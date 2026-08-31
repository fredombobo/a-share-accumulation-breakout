"""龙虎榜事件研究：下一开盘入场，匹配对照与原始收益并列（T09）。"""
from __future__ import annotations

import math
from typing import Any

from ab_screener.application.lhb_profiles import FILLED, next_open_return
from ab_screener.domain.lhb_contracts import OUTCOME_HORIZONS


def mean_ci(xs: list[float]) -> dict[str, float]:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "low": float("nan"), "high": float("nan")}
    mu = sum(xs) / n
    if n == 1:
        return {"n": 1, "mean": mu, "low": mu, "high": mu}
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    return {"n": n, "mean": mu, "low": mu - 1.96 * se, "high": mu + 1.96 * se}


def event_study(
    events: list[dict[str, Any]],
    *,
    bars: dict[str, dict[str, dict[str, Any]]],
    calendar: list[str],
    benchmark: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """events: {ts_code, disclose_date, matched: bool}."""
    buckets: dict[str, dict[int, list[float]]] = {
        "raw": {h: [] for h in OUTCOME_HORIZONS},
        "matched": {h: [] for h in OUTCOME_HORIZONS},
        "unmatched": {h: [] for h in OUTCOME_HORIZONS},
    }
    fill_stats = {"FILLED": 0, "UNFILLABLE": 0, "SUSPENDED": 0}
    for ev in events:
        ts = ev["ts_code"]
        for h in OUTCOME_HORIZONS:
            res = next_open_return(
                bars.get(ts, {}),
                signal_date=ev["disclose_date"],
                calendar=calendar,
                horizon=h,
                benchmark=benchmark,
            )
            st = res["status"]
            if h == 1:
                fill_stats[st] = fill_stats.get(st, 0) + 1
            if st != FILLED or res["raw"] is None:
                continue
            buckets["raw"][h].append(float(res["raw"]))
            key = "matched" if ev.get("matched") else "unmatched"
            buckets[key][h].append(float(res["raw"]))
    return {
        "horizons": {
            str(h): {
                "raw": mean_ci(buckets["raw"][h]),
                "matched_control": mean_ci(buckets["matched"][h]),
                "unmatched_raw": mean_ci(buckets["unmatched"][h]),
            }
            for h in OUTCOME_HORIZONS
        },
        "fill_stats": fill_stats,
        "n_events": len(events),
        "shows_matched_and_unmatched": True,
        "research_only": True,
    }


def tag_matched(
    events: list[dict[str, Any]],
    *,
    reason_field: str = "reason_code",
    date_field: str = "disclose_date",
    mv_field: str = "float_mv_yuan",
    mv_tol: float = 0.5,
) -> list[dict[str, Any]]:
    """按同原因/同日/市值相近打匹配对照标签。不把选择偏差当成席位 alpha。"""
    out: list[dict[str, Any]] = []
    for ev in events:
        item = dict(ev)
        if "matched" in item:
            out.append(item)
            continue
        peers = [
            other
            for other in events
            if other is not ev
            and other.get(reason_field) == ev.get(reason_field)
            and other.get(date_field) == ev.get(date_field)
        ]
        mv = ev.get(mv_field)
        if mv is not None:
            peers = [
                other
                for other in peers
                if other.get(mv_field) is not None
                and abs(float(other[mv_field]) - float(mv)) / max(float(mv), 1.0) <= mv_tol
            ]
        item["matched"] = bool(peers)
        out.append(item)
    return out
