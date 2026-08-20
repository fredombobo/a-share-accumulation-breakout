"""因子包（D 阶段）：注册表/alpha 衰减/组合优化。"""
from __future__ import annotations

from ab_screener.factors.registry import (  # noqa: F401
    FactorSpec,
    compute_factor,
    list_factors,
    momentum_n,
    moneyflow_net_ratio,
    quality_roe,
    register_factor,
    reversal_short,
    value_pb_inverse,
    volatility_20,
    volume_price_corr,
)
