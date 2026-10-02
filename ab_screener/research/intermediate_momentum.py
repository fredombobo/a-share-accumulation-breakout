"""Preregistered 6-minus-1-month cross-sectional momentum, no parameter search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

MECHANISM_ID = "INTERMEDIATE_MOMENTUM_SKIP_MONTH_V1"
SCORE = "_momentum_126_21"
RANK = "_momentum_rank_fraction"
COUNT = "_momentum_valid_count"


def attach_momentum_context(daily: pd.DataFrame, exchange_dates: list[str]) -> pd.DataFrame:
    """Rank each date using only prior returns, with missing sessions left missing."""
    required = {"ts_code", "trade_date", "pct_chg"}
    if not required.issubset(daily.columns) or len(set(exchange_dates)) != len(exchange_dates):
        raise ValueError("动量缺少收益数据或交易日重复")
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("动量行情主键重复")
    dates = sorted(exchange_dates)
    source = daily.copy()
    source["pct_chg"] = pd.to_numeric(source["pct_chg"], errors="coerce")
    returns = source.pivot(index="trade_date", columns="ts_code", values="pct_chg").reindex(dates) / 100
    valid = returns.where(np.isfinite(returns) & (returns > -1))
    scores = np.expm1(np.log1p(valid).rolling(105, min_periods=105).sum().shift(21))
    # A delisted/suspended/missing current bar cannot remain in today's ranking.
    scores = scores.where(valid.notna())
    ranks = scores.rank(axis=1, method="average", ascending=False, pct=True)
    counts = scores.count(axis=1)
    context = pd.DataFrame({SCORE: scores.stack(), RANK: ranks.stack()}).reset_index()
    context[COUNT] = context["trade_date"].map(counts)
    return daily.merge(context, on=["ts_code", "trade_date"], how="left", validate="one_to_one")


def evaluate_momentum(bars: pd.DataFrame, signal: dict[str, Any]) -> dict[str, Any]:
    day = "".join(ch for ch in str(signal.get("breakout_date") or "") if ch.isdigit())[:8]
    if not {"trade_date", SCORE, RANK, COUNT}.issubset(bars.columns):
        return {"passed": False, "reason": "MOMENTUM_CONTEXT_MISSING"}
    selected = bars[bars["trade_date"].astype(str).str.replace("-", "", regex=False) == day]
    if len(selected) != 1:
        return {"passed": False, "reason": "MOMENTUM_SIGNAL_BAR_NOT_UNIQUE"}
    row = selected.iloc[0]
    values = [row[SCORE], row[RANK], row[COUNT]]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in values):
        return {"passed": False, "reason": "MOMENTUM_HISTORY_INCOMPLETE"}
    result = {"signal_date": day, "score": float(row[SCORE]),
              "rank_fraction": float(row[RANK]), "valid_count": int(row[COUNT]),
              "passed": bool(row[COUNT] >= 50 and row[RANK] <= 0.30),
              "checks": [{"id": "minimum_cross_section", "passed": bool(row[COUNT] >= 50)},
                         {"id": "top_thirty_percent", "passed": bool(row[RANK] <= 0.30)}]}
    result["evidence_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
