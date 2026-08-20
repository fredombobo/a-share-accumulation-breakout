"""风险指标（P5.2）：TWR/波动/Sharpe/回撤/VaR95/CVaR95/集中度/流动性。

- 权重含现金合计 1 ± 1e-8。
- 缺估值/基准/现金流或最小样本 → 返回证据不足（INSUFFICIENT），不返回 0。
- 精确窗口、分位插值、损失符号、无风险率、年化因子由 robust_personal_v2.yaml 冻结
  （本模块以参数传入，缺省用保守默认）。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

MIN_SAMPLES = 30
ANNUAL_FACTOR = 252.0
RISK_FREE_ANNUAL = 0.02


class RiskMetricsError(ValueError):
    """指标输入非法（fail-closed）。"""


def _returns(equity_curve: list[float]) -> np.ndarray:
    arr = np.asarray(equity_curve, dtype=float)
    if arr.size < 2:
        raise RiskMetricsError("权益曲线至少需要 2 个点")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise RiskMetricsError("权益曲线含 NaN/Inf")
    if (arr <= 0).any():
        raise RiskMetricsError("权益曲线必须为正")
    return arr[1:] / arr[:-1] - 1.0


def twr(equity_curve: list[float]) -> float:
    """时间加权收益率。"""
    rets = _returns(equity_curve)
    return float(np.prod(1.0 + rets) - 1.0)


def _stats(equity_curve: list[float]) -> dict[str, Any]:
    rets = _returns(equity_curve)
    if len(rets) < MIN_SAMPLES:
        return {"status": "INSUFFICIENT", "reason": f"样本不足（{len(rets)} < {MIN_SAMPLES}）"}
    mean = float(rets.mean())
    std = float(rets.std(ddof=1))
    if std <= 0:
        return {"status": "INSUFFICIENT", "reason": "收益零方差"}
    sharpe_annual = (mean * ANNUAL_FACTOR - RISK_FREE_ANNUAL) / (std * math.sqrt(ANNUAL_FACTOR))
    drawdowns = []
    peak = 1.0
    eq = 1.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        drawdowns.append(1.0 - eq / peak)
    max_dd = float(max(drawdowns))
    # VaR95 / CVaR95（损失为正口径；收益 5% 分位；无损失风险时取 0）
    p5 = float(np.percentile(rets, 5, method="linear"))
    var95 = max(0.0, -p5)
    tail = rets[rets <= p5]
    cvar95 = max(0.0, -float(tail.mean())) if tail.size else var95
    return {
        "status": "OK",
        "twr": round(twr(equity_curve), 8),
        "volatility_annual": round(std * math.sqrt(ANNUAL_FACTOR), 6),
        "sharpe_annual": round(sharpe_annual, 4),
        "max_drawdown": round(max_dd, 6),
        "var95": round(var95, 6),
        "cvar95": round(cvar95, 6),
        "n_periods": len(rets),
        "annual_factor": ANNUAL_FACTOR,
        "risk_free_annual": RISK_FREE_ANNUAL,
    }


def portfolio_metrics(equity_curve: list[float]) -> dict[str, Any]:
    """组合指标（权益曲线口径）；证据不足返回 INSUFFICIENT 不填 0。"""
    try:
        return _stats(equity_curve)
    except RiskMetricsError as exc:
        return {"status": "INSUFFICIENT", "reason": str(exc)}


def weights_sum_to_one(cash_weight: float, position_weights: list[float]) -> float:
    """权重含现金合计（验收：1 ± 1e-8）。"""
    return cash_weight + sum(position_weights)


def concentration(weights: list[float]) -> dict[str, Any]:
    """集中度：最大权重 + HHI。"""
    if not weights:
        return {"status": "INSUFFICIENT", "reason": "无持仓权重"}
    arr = np.asarray(weights, dtype=float)
    if (arr < 0).any():
        raise RiskMetricsError("权重不能为负")
    total = float(arr.sum())
    if total <= 0:
        return {"status": "INSUFFICIENT", "reason": "权重合计非正"}
    norm = arr / total
    return {
        "status": "OK",
        "max_weight": round(float(norm.max()), 6),
        "hhi": round(float((norm ** 2).sum()), 6),
        "n_names": int(len(norm)),
    }


def liquidity_days(position_value_fen: int, daily_capacity_fen: int) -> float | None:
    """预计退出天数 = 市值 / 日容量；容量 ≤0 → 证据不足。"""
    if daily_capacity_fen <= 0:
        return None
    if position_value_fen <= 0:
        return 0.0
    return position_value_fen / daily_capacity_fen
