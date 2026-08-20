"""因子库（D 阶段）：注册表 + 计算器。

因子族：动量/反转/波动/价值/质量/资金流/量价。
计算器纯 pandas/numpy（离线可测）；真实因子值需数据积累。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

FactorFn = Callable[[pd.DataFrame], pd.Series | float]

_REGISTRY: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class FactorSpec:
    factor_id: str
    family: str
    description: str
    horizon_days: int


class FactorError(ValueError):
    """因子错误（fail-closed）。"""


def register_factor(spec: FactorSpec, compute_fn: FactorFn) -> None:
    if spec.factor_id in _REGISTRY:
        raise FactorError(f"重复注册因子: {spec.factor_id}")
    _REGISTRY[spec.factor_id] = {"spec": spec, "compute": compute_fn}


def list_factors() -> dict[str, FactorSpec]:
    return {k: v["spec"] for k, v in _REGISTRY.items()}


def compute_factor(factor_id: str, df: pd.DataFrame) -> Any:
    if factor_id not in _REGISTRY:
        raise FactorError(f"未知因子: {factor_id}（已注册: {sorted(_REGISTRY)}）")
    return _REGISTRY[factor_id]["compute"](df)


# ── 内置因子计算器 ──

def _close(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float)


def momentum_n(df: pd.DataFrame, n: int = 20) -> float:
    """N 日动量：close_t / close_{t-n} - 1。"""
    c = _close(df)
    if len(c) <= n or c.iloc[-n - 1] <= 0:
        return float("nan")
    return float(c.iloc[-1] / c.iloc[-n - 1] - 1.0)


def reversal_short(df: pd.DataFrame, n: int = 5) -> float:
    """短期反转：近 N 日收益（反转因子通常与之负相关）。"""
    return momentum_n(df, n)


def volatility_20(df: pd.DataFrame, n: int = 20) -> float:
    """20 日波动率（日收益 std）。"""
    rets = _close(df).pct_change().dropna().tail(n)
    if len(rets) < 5:
        return float("nan")
    return float(rets.std())


def volume_price_corr(df: pd.DataFrame, n: int = 20) -> float:
    """量价相关性：量变化与收益的相关（20 日）。"""
    if "vol" not in df.columns:
        return float("nan")
    close = _close(df).tail(n + 1)
    rets = close.pct_change().dropna()
    vols = df["vol"].astype(float).tail(n).values
    vol_chg = np.diff(vols) / np.maximum(vols[:-1], 1e-9)
    m = min(len(rets), len(vol_chg))
    if m < 5:
        return float("nan")
    return float(np.corrcoef(rets.values[-m:], vol_chg[-m:])[0, 1])


def moneyflow_net_ratio(df: pd.DataFrame, n: int = 5) -> float:
    """资金流净占比：近 N 日净流入额 / 成交额。"""
    if not {"net_mf_amount", "amount"}.issubset(df.columns):
        return float("nan")
    tail = df.tail(n)
    net = tail["net_mf_amount"].astype(float).sum()
    amt = tail["amount"].astype(float).sum()
    if amt <= 0:
        return float("nan")
    return float(net / amt)


def value_pb_inverse(df: pd.DataFrame) -> float:
    """价值（PB 倒数）：估值越低越高。"""
    if "pb" not in df.columns:
        return float("nan")
    pb = df["pb"].astype(float).iloc[-1]
    if pb <= 0 or np.isnan(pb):
        return float("nan")
    return float(1.0 / pb)


def quality_roe(df: pd.DataFrame) -> float:
    """质量（ROE 代理：收盘相对每股净资产无法直接算，用盈利质量占位）。"""
    if "roe" in df.columns:
        roe = df["roe"].astype(float).iloc[-1]
        return float(roe) if not np.isnan(roe) else float("nan")
    return float("nan")
