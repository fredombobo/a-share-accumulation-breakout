"""方案 B 入场引擎：小红书「五步抓主升」全流程量化

五步（与需求方确认的精确定义）：
1. 五日金叉定趋势：近 cross_lookback 日内发生过 ma5 上穿 ma10，且信号日 close > ma20
2. 建仓辨强弱：信号日前存在已终止建仓序列（bench_volume），放量柱 >= min_build_days 根
3. 量能破五再进攻：信号日 vol >= bench_vol × reattack_ratio 且 pct_chg >= chg_min（非涨停）

返回 dict 镜像 detect_accumulation_breakout 的核心契约（is_breakout/breakout_date/...），
并附带方案 B 专属字段（bench_vol/build_seq/cross_date），供 trade_sim bench 模式直接使用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bench_volume import find_build_seqs
from config import (
    BENCH_VOL_RATIO_MIN,
    PLAN_B_CHG_MIN,
    PLAN_B_CROSS_LOOKBACK,
    PLAN_B_MIN_BUILD_DAYS,
    PLAN_B_REATTACK_RATIO,
)


def detect_plan_b(
    df: pd.DataFrame,
    vol_ratio_min: float = BENCH_VOL_RATIO_MIN,
    cross_lookback: int = PLAN_B_CROSS_LOOKBACK,
    min_build_days: int = PLAN_B_MIN_BUILD_DAYS,
    reattack_ratio: float = PLAN_B_REATTACK_RATIO,
    chg_min: float = PLAN_B_CHG_MIN,
) -> dict:
    """对单只股票 K 线做方案 B 信号检测。df 列：date/open/high/low/close/vol（pct_chg 可选），升序。"""
    result: dict = {"is_breakout": False}
    n = len(df)
    if n < 40:
        result["reasons"] = ["样本不足"]
        return result

    d = df.reset_index(drop=True).copy()
    close = pd.to_numeric(d["close"], errors="coerce")
    vol = pd.to_numeric(d["vol"], errors="coerce")
    pct = pd.to_numeric(d["pct_chg"], errors="coerce") if "pct_chg" in d else close.pct_change() * 100.0

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()

    i = n - 1  # 信号日 = 窗口最后一日
    today_close, today_vol, today_pct = close.iloc[i], vol.iloc[i], pct.iloc[i]
    if pd.isna(today_close) or pd.isna(today_vol) or today_vol <= 0:
        result["reasons"] = ["信号日数据无效"]
        return result

    # ① 金叉定趋势：近 cross_lookback 日内 ma5 上穿 ma10
    cross_mask = (ma5 > ma10) & (ma5.shift(1) <= ma10.shift(1))
    recent = cross_mask.iloc[max(0, i - cross_lookback + 1): i + 1]
    cross_date = None
    if recent.any():
        cross_i = recent[recent].index[-1]
        cross_date = str(d["date"].iloc[cross_i])
    cond_cross = cross_date is not None
    cond_ma = bool(not pd.isna(ma20.iloc[i]) and today_close > ma20.iloc[i])

    # ② 建仓序列（信号日之前已终止的最后一个；信号日自身放量属「破五」不算建仓）
    seqs = find_build_seqs(d.iloc[: i + 1], vol_ratio_min=vol_ratio_min)
    prior = [s for s in seqs if s["end_i"] < i]
    seq = prior[-1] if prior else None
    cond_build = bool(seq and seq["n"] >= min_build_days)

    # ③ 量能破五：信号日量 >= 标杆 × reattack_ratio 且涨幅 >= chg_min（非一字涨停）
    cond_reattack = False
    if seq:
        limit_up = today_pct >= 9.8  # 涨停无法买入，排除
        cond_reattack = bool(
            today_vol >= seq["bench_vol"] * reattack_ratio
            and today_pct >= chg_min * 100.0
            and not limit_up
        )

    result.update({
        "is_breakout": bool(cond_cross and cond_ma and cond_build and cond_reattack),
        "breakout_date": str(d["date"].iloc[i]),
        "cross_date": cross_date,
        "bench_vol": seq["bench_vol"] if seq else None,
        "build_seq": seq,
        "ma5": float(ma5.iloc[i]) if not pd.isna(ma5.iloc[i]) else None,
        "ma20": float(ma20.iloc[i]) if not pd.isna(ma20.iloc[i]) else None,
        "latest_close": float(today_close),
        "latest_vol": float(today_vol),
        "cond_cross": cond_cross,
        "cond_ma": cond_ma,
        "cond_build": cond_build,
        "cond_reattack": cond_reattack,
    })
    if not result["is_breakout"]:
        fails = []
        if not cond_cross:
            fails.append(f"近{cross_lookback}日无金叉")
        if not cond_ma:
            fails.append("收盘未站上MA20")
        if not cond_build:
            fails.append("无合格建仓序列")
        if not cond_reattack:
            fails.append("量能破五未确认")
        result["reasons"] = fails
    return result
