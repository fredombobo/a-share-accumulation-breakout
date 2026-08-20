"""P3.2 v2 正式统计接线测试（validation.v2_statistics_block）。"""
from __future__ import annotations

import numpy as np

from ab_screener.research.validation import v2_statistics_block


def test_insufficient_samples_not_fabricated():
    block = v2_statistics_block([0.01, 0.02], n_trials=5)
    assert block["status"] == "INSUFFICIENT"
    assert "样本不足" in block["reason"]


def test_zero_variance_insufficient():
    block = v2_statistics_block([0.001] * 40, n_trials=5)
    assert block["status"] == "INSUFFICIENT"


def test_ok_block_with_positive_edge():
    rng = np.random.default_rng(9)
    returns = rng.normal(0.002, 0.01, size=200).tolist()
    block = v2_statistics_block(returns, n_trials=1)
    assert block["status"] == "OK"
    assert block["n_periods"] == 200
    assert block["dsr"] > 0.9
    assert block["min_track_record_length"] > 0
    assert block["min_track_record_coverage"] is not None


def test_trials_deflate_dsr():
    rng = np.random.default_rng(9)
    returns = rng.normal(0.002, 0.01, size=200).tolist()
    single = v2_statistics_block(returns, n_trials=1)
    many = v2_statistics_block(returns, n_trials=500)
    assert many["dsr"] < single["dsr"]
