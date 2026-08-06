"""标杆量四象限引擎（小红书「标杆量识破洗盘」体系的量化实现）

核心概念（已与需求方逐条确认的精确定义）：
- 建仓日：vol_ratio = 当日量 / 5日均量 >= vol_ratio_min 且 pct_chg > 0
- 断档容忍：序列中允许 1 天「量 < 5日均量 且 pct_chg > -2%」的缩量小阴，第 2 个断档日终止序列
- 标杆量（N-1 原则）：建仓序列终止时一次性锁定 = 序列内倒数第 2 根放量柱的量能（N==1 时用当天）
- 四象限（持有期逐日）：
    量 < 标杆 且收阳 → PUSH（拉升，持有）
    量 < 标杆 且收阴 → WASH（洗盘，持有）
    量 >= 标杆（无论阴阳）→ DIST（出货，预警）
- 二次出货出场：exit_window 个交易日内累计 2 次 DIST → 次日开盘清仓；
  中间出现 >= strong_reset 根连续非 DIST 强势日 → 计数清零
- 兜底：固定止损（-stop_pct）与最长持有强平由 trade_sim 统一处理，优先级 stop → bench → time
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    BENCH_EXIT_WINDOW,
    BENCH_GAP_MAX_PCT,
    BENCH_STRONG_RESET,
    BENCH_VOL_RATIO_MIN,
)

PUSH, WASH, DIST = "PUSH", "WASH", "DIST"


def _vol_ratio(vol: pd.Series, window: int = 5) -> pd.Series:
    """当日量 / 前 window 日均量（不含当日，避免当日天量自我稀释）。"""
    ma = vol.rolling(window).mean().shift(1)
    return vol / ma.replace(0, np.nan)


def find_build_seqs(
    df: pd.DataFrame,
    vol_ratio_min: float = BENCH_VOL_RATIO_MIN,
    gap_max_pct: float = BENCH_GAP_MAX_PCT,
) -> list[dict]:
    """扫描全窗口，返回所有建仓序列（按时间升序）。

    每个序列: {start_i, end_i, n, bench_vol, bench_i}
    - start_i/end_i: 序列首尾（含断档日）的行索引
    - n: 放量柱（建仓日）根数
    - bench_i: 标杆柱索引（倒数第 2 根放量柱；n==1 时为唯一放量柱）
    - bench_vol: 标杆量
    """
    vol = pd.to_numeric(df["vol"], errors="coerce").reset_index(drop=True)
    pct = pd.to_numeric(df.get("pct_chg"), errors="coerce").reset_index(drop=True)
    if pct.isna().all() and "close" in df:
        pct = pd.to_numeric(df["close"], errors="coerce").reset_index(drop=True).pct_change() * 100.0
    vr = _vol_ratio(vol).reset_index(drop=True)

    seqs: list[dict] = []
    exp_idx: list[int] = []          # 当前序列放量柱索引
    gap_used = 0
    start_i = -1

    def _close_seq(end_i: int) -> None:
        nonlocal exp_idx, gap_used, start_i
        if exp_idx:
            n = len(exp_idx)
            bench_i = exp_idx[-2] if n >= 2 else exp_idx[0]
            seqs.append({
                "start_i": start_i,
                "end_i": end_i,
                "n": n,
                "bench_vol": float(vol.iloc[bench_i]),
                "bench_i": bench_i,
            })
        exp_idx, gap_used, start_i = [], 0, -1

    for i in range(len(vol)):
        v, p, r = vol.iloc[i], pct.iloc[i], vr.iloc[i]
        if pd.isna(v) or v <= 0:
            _close_seq(i - 1)          # 停牌/零量：序列终止
            continue
        if pd.isna(p) or pd.isna(r):
            continue                   # 头部均线未成形，不开启也不终止
        is_expand = (r >= vol_ratio_min) and (p > 0)
        # 断档日：量 < 5日均量（vr < 1）且跌幅 < gap_max_pct
        is_gap_ok = (r < 1.0) and (p > gap_max_pct * 100.0)
        if is_expand:
            if start_i < 0:
                start_i = i
            exp_idx.append(i)
            gap_used = 0
        elif start_i >= 0 and is_gap_ok and gap_used == 0:
            gap_used = 1               # 容忍 1 天断档
        elif start_i >= 0:
            _close_seq(i - 1)          # 第 2 个断档日或规则违反：终止
    if exp_idx:
        _close_seq(len(vol) - 1)
    return seqs


def detect_build_seq(df: pd.DataFrame, vol_ratio_min: float = BENCH_VOL_RATIO_MIN) -> dict:
    """返回窗口内最后一个建仓序列（供入场检测用）。无则 found=False。"""
    seqs = find_build_seqs(df, vol_ratio_min=vol_ratio_min)
    if not seqs:
        return {"found": False}
    return {"found": True, **seqs[-1]}


def classify_holding_day(vol: float, pct_chg: float, bench_vol: float) -> str:
    """四象限判定（单日）。"""
    if vol >= bench_vol:
        return DIST
    return PUSH if pct_chg > 0 else WASH


def bench_exit_events(
    df: pd.DataFrame,
    entry_i: int,
    bench_vol: float,
    exit_window: int = BENCH_EXIT_WINDOW,
    strong_reset: int = BENCH_STRONG_RESET,
    max_hold: int = 30,
) -> dict:
    """标杆量出场模拟：从 entry_i 之后逐日判定（不含入场日本身的买入逻辑，由 trade_sim 控制价格）。

    返回 {exit_j, exit_type, dist_first_i, days}：
    - exit_type: "bench"（窗口内二次出货）| "time"（超时）
    - 固定止损不在此处判定（需要 entry 价），由 trade_sim 在外层优先判定
    """
    vol = pd.to_numeric(df["vol"], errors="coerce").reset_index(drop=True)
    pct = pd.to_numeric(df.get("pct_chg"), errors="coerce").reset_index(drop=True)
    last_j = min(len(vol) - 1, entry_i + max_hold)
    dist_first_i: int | None = None
    strong_run = 0

    for j in range(entry_i + 1, last_j + 1):
        v, p = vol.iloc[j], pct.iloc[j]
        if pd.isna(v) or pd.isna(p):
            continue
        cls = classify_holding_day(v, p, bench_vol)
        if cls == DIST:
            strong_run = 0
            if dist_first_i is None:
                dist_first_i = j
            elif j - dist_first_i <= exit_window:
                return {"exit_j": j, "exit_type": "bench", "days": j - entry_i}
            else:
                dist_first_i = j       # 超出窗口：视为新的第 1 次
        else:
            strong_run += 1
            if strong_run >= strong_reset:
                dist_first_i = None    # 连续强势日清零计数
    return {"exit_j": last_j, "exit_type": "time", "days": last_j - entry_i}
