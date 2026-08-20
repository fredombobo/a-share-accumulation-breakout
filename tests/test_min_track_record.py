"""P3.2 MinTRL 测试：参考公式、正态退化、fail-closed。"""
from __future__ import annotations

import pytest
from scipy import stats

from ab_screener.research.min_track_record import (
    min_track_record_length,
    min_track_record_summary,
)


def test_mintrl_normal_reference():
    """skew=0, kurt=3, SR=1, α=0.95 → n* = 1 + [1+(kurt-1)/4·SR²]·(z/SR)² = 1 + 1.5·z²。"""
    z = stats.norm.ppf(0.95)
    expected = 1.0 + 1.5 * z * z
    n = min_track_record_length(1.0, 0.0, 3.0, confidence=0.95)
    assert abs(n - expected) < 1e-9
    assert n == pytest.approx(5.0584, abs=1e-3)


def test_mintrl_lower_sharpe_needs_more():
    n_high = min_track_record_length(1.0, 0.0, 3.0)
    n_low = min_track_record_length(0.5, 0.0, 3.0)
    assert n_low > n_high


def test_mintrl_fat_tail_penalizes():
    normal = min_track_record_length(1.0, 0.0, 3.0)
    fat = min_track_record_length(1.0, -0.5, 6.0)
    assert fat > normal


def test_mintrl_fail_closed():
    with pytest.raises(ValueError, match="有限值"):
        min_track_record_length(float("inf"), 0.0, 3.0)
    with pytest.raises(ValueError, match="必须为正"):
        min_track_record_length(0.0, 0.0, 3.0)
    with pytest.raises(ValueError, match="置信水平"):
        min_track_record_length(1.0, 0.0, 3.0, confidence=1.0)


def test_mintrl_summary():
    s = min_track_record_summary(sharpe=1.0, skew=0.0, kurtosis=3.0)
    assert s["min_track_record_length"] == pytest.approx(5.06, abs=0.01)
