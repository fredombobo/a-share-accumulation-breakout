"""P3.2 DSR 测试：参考值、正态退化情形、fail-closed。"""
from __future__ import annotations

import math

import pytest
from scipy import stats

from ab_screener.research.deflated_sharpe import (
    deflated_sharpe,
    dsr_summary,
    expected_max_sharpe_null,
)


def test_dsr_normal_case_hand_calc():
    """skew=0, kurt=3（正态）→ DSR = Φ((SR-SR0)·√(n-1))。"""
    sr, n, trials = 1.0, 100, 1
    dsr = deflated_sharpe(sr, n, skew=0.0, kurtosis=3.0, n_trials=trials)
    expected = stats.norm.cdf(1.0 * math.sqrt(99))
    assert abs(dsr - expected) < 1e-9


def test_dsr_deflation_with_trials():
    """多试验后 SR0 抬高 → DSR 显著下降。"""
    base = deflated_sharpe(1.0, 100, 0.0, 3.0, n_trials=1)
    deflated = deflated_sharpe(1.0, 100, 0.0, 3.0, n_trials=50)
    assert deflated < base
    # 50 次试验的 SR0 为正
    assert expected_max_sharpe_null(50) > 0


def test_dsr_skew_kurtosis_penalize():
    """负偏/厚尾 → DSR 降低（非正态校正；低 SR 下差异可见）。"""
    normal = deflated_sharpe(0.3, 200, 0.0, 3.0, n_trials=1)
    fat_tail = deflated_sharpe(0.3, 200, -0.5, 6.0, n_trials=1)
    assert fat_tail < normal


def test_dsr_fail_closed():
    with pytest.raises(ValueError, match="有限值"):
        deflated_sharpe(float("nan"), 100, 0.0, 3.0, n_trials=1)
    with pytest.raises(ValueError, match="样本期数"):
        deflated_sharpe(1.0, 1, 0.0, 3.0, n_trials=1)
    with pytest.raises(ValueError, match="分母非正"):
        deflated_sharpe(5.0, 100, skew=0.0, kurtosis=0.5, n_trials=1)


def test_dsr_summary_shape():
    s = dsr_summary(sharpe=1.0, n_periods=100, skew=0.0, kurtosis=3.0, n_trials=10)
    assert "deflated_sharpe" in s and "sr0_null_max" in s
