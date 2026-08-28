"""Causal market-regime entry filter for authoritative Breakout research.

The filter consumes only the frozen point-in-time CSI300 series.  A signal on
date ``t`` is eligible only when the production market-regime classifier,
evaluated with benchmark closes through ``t``, allows new entries.  Missing
history is a hard research error rather than a neutral-market fallback.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from ab_screener.market_regime import (
    MARKET_REGIME_MA_WINDOW,
    MARKET_REGIME_MINIMUM_HISTORY_ROWS,
    MARKET_REGIME_RETURN_LOOKBACK,
    classify_regime_point,
    market_regime_policy_identity,
)
from ab_screener.research.pit_reader import ResearchPitError

if TYPE_CHECKING:
    from ab_screener.research.pit_reader import ResearchPitSnapshot

RESEARCH_REGIME_FILTER_VERSION = "research-market-regime-v1.1.0"
PRODUCTION_REGIME_ENTRY_POLICY = "production"
ATTACK_ONLY_REGIME_ENTRY_POLICY = "attack_only"
SUPPORTED_REGIME_ENTRY_POLICIES = frozenset(
    {PRODUCTION_REGIME_ENTRY_POLICY, ATTACK_ONLY_REGIME_ENTRY_POLICY}
)


@dataclass(frozen=True)
class ResearchRegimeFilter:
    """Immutable allowed signal dates plus preregistration evidence."""

    allowed_signal_dates: frozenset[str]
    blocked_signal_dates: frozenset[str]
    evidence: dict[str, Any]

    def identity(self) -> dict[str, Any]:
        return dict(self.evidence)


def build_research_regime_filter(
    snapshot: ResearchPitSnapshot,
    *,
    start: str,
    end: str,
    entry_policy: str = PRODUCTION_REGIME_ENTRY_POLICY,
) -> ResearchRegimeFilter:
    """Build a deterministic, fail-closed filter for one research window."""
    policy_name = str(entry_policy).strip().lower()
    if policy_name not in SUPPORTED_REGIME_ENTRY_POLICIES:
        raise ResearchPitError(f"不支持的市场状态入场策略: {entry_policy!r}")
    start_date = _date(start)
    end_date = _date(end)
    if start_date > end_date:
        raise ResearchPitError("市场状态研究窗口起止日期倒置")
    benchmark = snapshot.load_benchmark(start=snapshot.data_start, end=end_date)
    code = str(snapshot.benchmark_code or "").upper()
    if not code or benchmark.empty:
        raise ResearchPitError("权威研究缺少冻结的沪深300 PIT 行情")

    required_dates = snapshot.distinct_dates(start=start_date, end=end_date)
    if not required_dates:
        raise ResearchPitError("市场状态研究窗口内没有股票交易日")

    frame = benchmark.copy()
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["trade_date"].duplicated().any():
        raise ResearchPitError("冻结基准行情存在重复交易日")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ResearchPitError("冻结基准行情存在非法收盘价")
    frame = frame.sort_values("trade_date").reset_index(drop=True)

    available_dates = set(frame["trade_date"])
    missing = sorted(set(required_dates) - available_dates)
    if missing:
        raise ResearchPitError(f"沪深300 PIT 行情未覆盖研究交易日: {missing[:5]}")

    min_rows = MARKET_REGIME_MINIMUM_HISTORY_ROWS
    ma_window = MARKET_REGIME_MA_WINDOW
    return_lookback = MARKET_REGIME_RETURN_LOOKBACK
    first_index = int(frame.index[frame["trade_date"] == required_dates[0]][0])
    if first_index + 1 < min_rows:
        raise ResearchPitError(
            f"沪深300 PIT 前置历史不足: 首个研究日仅有 {first_index + 1} 行，需要 {min_rows} 行"
        )

    closes = frame["close"].astype(float)
    ma_values = closes.rolling(ma_window).mean()
    prior_values = closes.shift(return_lookback)
    index_by_date = {date: int(index) for index, date in enumerate(frame["trade_date"])}
    allowed: set[str] = set()
    blocked: set[str] = set()
    counts = {"attack": 0, "neutral": 0, "defense": 0}
    for trade_date in required_dates:
        index = index_by_date[trade_date]
        close = float(closes.iloc[index])
        ma20 = float(ma_values.iloc[index])
        prior = float(prior_values.iloc[index])
        if not all(pd.notna(value) for value in (close, ma20, prior)) or prior <= 0:
            raise ResearchPitError(f"沪深300 PIT 无法计算市场状态: {trade_date}")
        ret_20d = close / prior - 1.0
        regime, _label, production_allow, _slots, _notes = classify_regime_point(
            close,
            ma20,
            ret_20d,
        )
        counts[regime] += 1
        allow = production_allow if policy_name == PRODUCTION_REGIME_ENTRY_POLICY else regime == "attack"
        (allowed if allow else blocked).add(trade_date)

    allowed_hash = hashlib.sha256("\n".join(sorted(allowed)).encode("ascii")).hexdigest()
    policy = market_regime_policy_identity()
    identity_payload = {
        "version": RESEARCH_REGIME_FILTER_VERSION,
        "entry_policy": policy_name,
        "production_policy_version": policy["version"],
        "production_policy_hash": policy["config_hash"],
        "benchmark_code": code,
        "benchmark_sha256": snapshot.benchmark_sha256,
        "decision_at": snapshot.decision_at,
        "start": start_date,
        "end": end_date,
        "required_dates": len(required_dates),
        "allowed_dates": len(allowed),
        "blocked_dates": len(blocked),
        "regime_counts": counts,
        "allowed_dates_sha256": allowed_hash,
    }
    identity_payload["identity_sha256"] = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return ResearchRegimeFilter(
        allowed_signal_dates=frozenset(allowed),
        blocked_signal_dates=frozenset(blocked),
        evidence=identity_payload,
    )


def _date(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 8:
        raise ResearchPitError(f"日期字段非法: {value!r}")
    return digits[:8]
