"""P3.2 嵌套 Walk-Forward 测试：训练/测试分离、无重叠、fail-closed。"""
from __future__ import annotations

import numpy as np
import pytest

from ab_screener.research.nested_walkforward import (
    NestedWalkforwardError,
    nested_walkforward,
)


def test_windows_no_overlap_and_test_only_once():
    t = 300
    rng = np.random.default_rng(5)
    returns = rng.normal(0.0005, 0.01, size=t)
    result = nested_walkforward(returns, n_test_windows=3, train_frac=0.6)
    windows = result["windows"]
    assert len(windows) == 3
    # 测试窗互不重叠且升序
    for i in range(1, len(windows)):
        assert windows[i]["test_start"] >= windows[i - 1]["test_end"]
    # 训练折 = 测试窗前全部历史（train_end == test_start，无重叠）
    for w in windows:
        assert w["train_end"] == w["test_start"]
        assert w["train_start"] == 0
    assert result["verdict"] in ("PASS", "FAIL")


def test_selector_only_on_train_fold():
    """selector 只能看到训练折（扩展窗：测试前全部历史）。"""
    t = 300
    returns = np.arange(t, dtype=float) * 0.001  # 单调递增
    seen: list[int] = []

    def selector(train):
        seen.append(len(train))
        return float(train.mean())

    result = nested_walkforward(returns, n_test_windows=3, train_frac=0.6,
                                selector=selector, min_train_periods=20)
    # 训练折随窗扩展（非满）：len(train) 递增
    assert seen[0] < seen[1] < seen[2]
    assert result["verdict"] == "PASS"  # 上升序列 → 测试折全正


def test_fail_closed():
    with pytest.raises(NestedWalkforwardError, match="NaN"):
        arr = np.zeros(40)
        arr[3] = np.nan
        nested_walkforward(arr, 2)
    with pytest.raises(NestedWalkforwardError, match="测试窗数"):
        nested_walkforward(np.zeros(40), 1)
    with pytest.raises(NestedWalkforwardError, match="样本不足"):
        nested_walkforward(np.zeros(10), 5, train_frac=0.9)
