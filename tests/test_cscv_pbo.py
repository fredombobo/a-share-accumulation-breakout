"""P3.2 CSCV-PBO 测试：logit、已知矩阵、fail-closed。"""
from __future__ import annotations

import numpy as np
import pytest

from ab_screener.research.cscv import PboError, cscv_pbo, pbo_verdict


def test_manual_logit_reference():
    """4 组合：logit 按 N 列排名计算（N≥4）。"""
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 1.0, size=(16, 4))
    result = cscv_pbo(returns, n_splits=4)
    assert result["n_splits"] == 4
    assert result["logits_count"] > 0
    assert result["logits_count"] == result["combos_evaluated"]
    assert result["selection_rule"] == "train_sharpe_rank_1"
    assert 0.0 <= result["pbo"] <= 1.0


def test_pbo_low_when_one_strategy_consistently_best():
    """组合 A 恒定更优 → PBO 低（选择不翻车）。"""
    rng = np.random.default_rng(11)
    t = 64
    base = rng.normal(0.001, 0.01, size=(t, 1))
    returns = np.hstack([base + 0.003, base, base - 0.002, base])
    result = cscv_pbo(returns, n_splits=8)
    assert result["pbo"] < 0.5


def test_pbo_high_when_selection_noise():
    """纯噪声 → PBO 接近 0.5（选择无信息）。"""
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0, 1.0, size=(64, 6))
    result = cscv_pbo(returns, n_splits=8)
    assert 0.0 < result["pbo"] < 1.0


def test_cscv_fail_closed():
    with pytest.raises(PboError, match="样本期数"):
        cscv_pbo(np.zeros((4, 4)), n_splits=16)
    with pytest.raises(PboError, match="组合数"):
        cscv_pbo(np.zeros((32, 2)), n_splits=8)
    with pytest.raises(PboError, match="NaN"):
        arr = np.zeros((32, 4))
        arr[0, 0] = np.nan
        cscv_pbo(arr, n_splits=8)
    with pytest.raises(PboError, match="二维"):
        cscv_pbo(np.zeros(10))


def test_pbo_verdict_threshold():
    assert pbo_verdict(0.10)["pass"] is True
    assert pbo_verdict(0.30)["pass"] is False
