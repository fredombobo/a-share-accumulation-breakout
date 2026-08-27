"""正式统计：嵌套 Walk-Forward（P3.2）。

规则：
- 测试窗 = 初始训练期之后的等宽窗口；训练折 = 测试窗前全部历史（扩展窗，
  无日期重叠，测试折只评估一次、绝不参与选择）。
- 样本不足 / NaN / 测试窗 <2 → FAIL（不静默降级）。
"""
from __future__ import annotations

from typing import Any

import numpy as np


class NestedWalkforwardError(ValueError):
    """嵌套 WF 输入非法（fail-closed）。"""


def nested_walkforward(
    returns: np.ndarray,
    n_test_windows: int,
    *,
    train_frac: float = 0.6,
    min_train_periods: int = 20,
    selector=None,
) -> dict[str, Any]:
    """returns: (T,) 单组合收益序列。selector(train_returns) -> float 用于训练折选参。"""
    arr = np.asarray(returns, dtype=float).reshape(-1)
    if arr.ndim != 1 or arr.size == 0:
        raise NestedWalkforwardError("收益序列为空或形状非法")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise NestedWalkforwardError("收益序列含 NaN/Inf（拒绝静默降级）")
    if n_test_windows < 2:
        raise NestedWalkforwardError("测试窗数必须 ≥2")
    t = arr.size
    initial_train = max(min_train_periods, int(round(t * train_frac)))
    available = t - initial_train
    if available < n_test_windows:
        raise NestedWalkforwardError("样本不足：初始训练期 + 测试窗超出样本长度")
    window_len = available // n_test_windows
    if window_len < 1:
        raise NestedWalkforwardError("测试窗过短（样本不足）")

    windows: list[dict[str, Any]] = []
    for i in range(n_test_windows):
        test_start = initial_train + i * window_len
        test_end = min(test_start + window_len, t)
        train = arr[0:test_start]  # 扩展窗：测试前全部历史（无重叠）
        if len(train) < min_train_periods:
            raise NestedWalkforwardError(
                f"训练样本不足: {len(train)} < {min_train_periods}（窗 {i}）"
            )
        train_metric = float(selector(train)) if selector is not None else float(train.mean())
        test_metric = float(arr[test_start:test_end].mean())
        windows.append(
            {
                "window": i,
                "train_start": 0,
                "train_end": int(test_start),
                "test_start": int(test_start),
                "test_end": int(test_end),
                "train_metric": round(train_metric, 6),
                "test_metric": round(test_metric, 6),
            }
        )
    positive_test = [w for w in windows if w["test_metric"] > 0]
    ratio = len(positive_test) / len(windows)
    verdict = "PASS" if ratio >= 0.6 else "FAIL"
    return {
        "windows": windows,
        "positive_test_ratio": round(ratio, 4),
        "verdict": verdict,
        "reason": "正收益测试窗比例" if verdict == "PASS" else "正收益测试窗比例不足 60%",
    }


def nested_parameter_walkforward(
    returns: np.ndarray,
    param_ids: list[str],
    *,
    n_test_windows: int = 5,
    train_frac: float = 0.5,
    min_train_periods: int = 40,
) -> dict[str, Any]:
    """Select a parameter on each expanding train fold and score it once on test.

    ``returns`` is a T×N matrix.  Selection uses compounded train return only;
    every non-overlapping test fold remains unread until its parameter is frozen.
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 2:
        raise NestedWalkforwardError("参数收益矩阵必须为二维 (T, N)")
    periods, parameters = arr.shape
    if parameters < 2 or len(param_ids) != parameters or len(set(param_ids)) != parameters:
        raise NestedWalkforwardError("参数标识与收益矩阵列不一致或不唯一")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise NestedWalkforwardError("参数收益矩阵含 NaN/Inf（拒绝静默降级）")
    if np.any(arr <= -1.0):
        raise NestedWalkforwardError("单期收益必须大于 -100%")
    if n_test_windows < 2:
        raise NestedWalkforwardError("测试窗数必须 ≥2")
    if not 0.0 < train_frac < 1.0:
        raise NestedWalkforwardError("train_frac 必须位于 (0, 1)")
    initial_train = max(min_train_periods, int(round(periods * train_frac)))
    available = periods - initial_train
    if available < n_test_windows:
        raise NestedWalkforwardError("样本不足：初始训练期 + 测试窗超出样本长度")
    window_len = available // n_test_windows
    if window_len < 1:
        raise NestedWalkforwardError("测试窗过短（样本不足）")

    windows: list[dict[str, Any]] = []
    for index in range(n_test_windows):
        test_start = initial_train + index * window_len
        test_end = periods if index == n_test_windows - 1 else test_start + window_len
        train = arr[:test_start, :]
        if len(train) < min_train_periods:
            raise NestedWalkforwardError(
                f"训练样本不足: {len(train)} < {min_train_periods}（窗 {index}）"
            )
        train_scores = np.expm1(np.log1p(train).sum(axis=0))
        selected_index = int(np.argmax(train_scores))
        selected_id = param_ids[selected_index]
        test_return = float(np.expm1(np.log1p(arr[test_start:test_end, selected_index]).sum()))
        windows.append(
            {
                "window": index + 1,
                "train_start": 0,
                "train_end": int(test_start),
                "test_start": int(test_start),
                "test_end": int(test_end),
                "selected_param_id": selected_id,
                "selected_train_return": round(float(train_scores[selected_index]), 8),
                "test_return": round(test_return, 8),
            }
        )
    positive_ratio = sum(float(row["test_return"]) > 0 for row in windows) / len(windows)
    return {
        "windows": windows,
        "positive_test_ratio": round(positive_ratio, 4),
        "verdict": "PASS" if positive_ratio >= 0.6 else "FAIL",
        "selection_rule": "max_compounded_train_return_then_frozen_test",
    }
