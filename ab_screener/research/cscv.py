"""正式统计：CSCV-PBO 组合净化交叉验证（P3.2）。

算法（Bailey et al., "The Probability of Backtest Overfitting"）：
1. T 期收益矩阵（T×N，N 个参数组合）切成 S 个连续块。
2. 取 C=S/2 的所有块组合（对称组合）；每个组合：train=组合内块，test=其余块。
3. 对每个组合：按 train 期每列 Sharpe 排序 → 取表现最好的半数列（top-k）；
   计算每列的 logit = ln( rank_test / (N - rank_test) )，rank_test 为该列在 test
   期的性能排名。
4. PBO = P(logit ≤ 0)（logit 分布中小于等于 0 的比例）。
参数只能在训练折选择，测试折只评估一次（不参与选择）。
NaN 行 → ValueError（fail-closed，不静默降级）。
"""
from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np


class PboError(ValueError):
    """CSCV-PBO 输入非法（fail-closed）。"""


def _column_sharpe(block: np.ndarray) -> np.ndarray:
    """每列（参数组合）在给定块内的 Sharpe（按行收益，不做年化）。"""
    means = block.mean(axis=0)
    stds = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(stds > 0, means / stds, 0.0)
    return sharpe


def cscv_pbo(
    returns: np.ndarray,
    n_splits: int = 16,
) -> dict[str, Any]:
    """returns: (T 期, N 组合) 收益矩阵；返回 {pbo, logits, n_splits}。"""
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 2:
        raise PboError("收益矩阵必须为二维 (T, N)")
    t, n = arr.shape
    if t < n_splits:
        raise PboError(f"样本期数 {t} < 分块数 {n_splits}（样本不足）")
    if n < 4:
        raise PboError(f"参数组合数 {n} < 4（CSCV 至少需要 4 个组合，k=N/2≥2）")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise PboError("收益矩阵含 NaN/Inf（拒绝静默降级）")

    blocks = np.array_split(arr, n_splits, axis=0)
    half = n_splits // 2
    logits: list[float] = []
    combo_count = 0
    # 对称组合：C(S, S/2) 中取一半（反向组合冗余），限定组合数上限防爆炸
    max_combos = 2000
    all_combos = list(itertools.combinations(range(n_splits), half))
    if len(all_combos) > max_combos:
        step = len(all_combos) // max_combos
        all_combos = all_combos[::step]
    for combo in all_combos:
        test_blocks = [i for i in range(n_splits) if i not in combo]
        train = np.vstack([blocks[i] for i in combo])
        test = np.vstack([blocks[i] for i in test_blocks])
        train_sr = _column_sharpe(train)
        test_sr = _column_sharpe(test)
        # 训练折选择 top-k（前 50%），测试折只评估一次
        k = n // 2
        chosen = np.argsort(train_sr)[::-1][:k]
        # 测试折全部 N 列的升序排名（rank 1 = 最差，与 CSCV 论文口径一致）
        order = np.argsort(test_sr)
        ranks = np.empty(n, dtype=int)
        ranks[order] = np.arange(1, n + 1)
        for col in chosen:
            rank = int(ranks[col])
            if 0 < rank < n:
                logit = math.log(rank / (n - rank))
            else:
                logit = math.log((n - 0.5) / 0.5) if rank >= n else math.log(0.5 / (n - 0.5))
            logits.append(logit)
        combo_count += 1

    if not logits:
        raise PboError("无可用的训练/测试组合")
    pbo = float(np.mean([lg <= 0 for lg in logits]))
    return {
        "pbo": round(pbo, 4),
        "logit_median": round(float(np.median(logits)), 4),
        "n_splits": n_splits,
        "combos_evaluated": combo_count,
        "logits_count": len(logits),
    }


def pbo_verdict(pbo: float) -> dict[str, Any]:
    """PBO 判定：≤0.20 → PASS（ROBUST_PERSONAL_V2 口径）。"""
    return {
        "pbo": pbo,
        "pass": pbo <= 0.20,
        "verdict": "PASS" if pbo <= 0.20 else "FAIL",
    }
