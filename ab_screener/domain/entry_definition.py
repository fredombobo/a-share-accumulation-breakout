"""A 池入场定义 v1 — 扫描 / 回测 / 纸面 / 归因 的单一真相源。

冻结规则见 docs/ENTRY-DEFINITION-V1.md。

硬约束：
- A 池信号 = detect_accumulation_breakout 默认（strict）结构要求
- 信号日 = breakout_date 对应 K 线
- 入场 = 信号日**下一交易日**开盘（无 open 则用 close）
- 不得用「采样日+1」替代突破日+1
- Lab 参数优化不得改变本入场定义；只能改出场或阈值网格
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from config import (
    BENCH_EXIT_WINDOW,
    BENCH_MAX_HOLD_DAYS,
    BENCH_STOP_PCT,
    BENCH_STRONG_RESET,
    BOX_MAX_AMP,
    BOX_MAX_DAYS,
    BOX_MAX_MID_DRAWDOWN,
    BOX_MIN_DAYS,
    BOX_POS_TREND_LOOKBACK,
    BOX_POS_TREND_MAX_DROP,
    BREAKOUT_CHG_MAX,
    BREAKOUT_CHG_MIN,
    BREAKOUT_VOL_RATIO,
    BREAKOUT_VS_RECENT_VOL_RATIO,
    FUND_FLOW_DAYS,
    FUND_FLOW_MIN_RATIO,
    FUND_SCORE_WEIGHT,
    FUNDAMENTAL_SCORE_WEIGHT,
    HORIZON_DAYS,
    INCLUDE_RELAXED_IN_A,
    MAX_HOLD_DAYS,
    MAX_MV_YI,
    MAX_PB,
    MAX_PE,
    MIN_LIST_DAYS,
    MIN_MV_YI,
    MIN_PB,
    MIN_PE,
    MIN_PRICE,
    SIGNAL_SCORE_WEIGHT,
    STOP_LOSS_PCT,
    TARGET_PCT_1,
    TOP_N_TRADE,
    TREND_SLOPE_LIMIT,
)

# 与 signals.BREAKOUT_WINDOW_DAYS 对齐（signals 模块常量，避免循环 import 时写死）
BREAKOUT_WINDOW_DAYS = 5

ENTRY_DEFINITION_VERSION = "v1"
ENTRY_DEFINITION_ID = "A_POOL_STRICT_NEXT_OPEN_V1"
ENTRY_TIMING = "next_open"  # 信号日下一交易日开盘
SIGNAL_ENGINE = "signals.detect_accumulation_breakout"
SIGNAL_PROFILE = "strict"


def definition_snapshot() -> dict[str, Any]:
    """可序列化快照，写入报告 / fingerprint。"""
    return {
        "version": ENTRY_DEFINITION_VERSION,
        "id": ENTRY_DEFINITION_ID,
        "signal_engine": SIGNAL_ENGINE,
        "signal_profile": SIGNAL_PROFILE,
        "entry_timing": ENTRY_TIMING,
        "entry_rule": "breakout_date 对应 bar 的下一交易日开盘价；无 open 用 close",
        "breakout_window_days": BREAKOUT_WINDOW_DAYS,
        "horizon_days": HORIZON_DAYS,
        "box": {
            "min_days": BOX_MIN_DAYS,
            "max_days": BOX_MAX_DAYS,
            "max_amp": BOX_MAX_AMP,
            "trend_slope_limit": TREND_SLOPE_LIMIT,
            "max_mid_drawdown": BOX_MAX_MID_DRAWDOWN,
            "pos_trend_lookback": BOX_POS_TREND_LOOKBACK,
            "pos_trend_max_drop": BOX_POS_TREND_MAX_DROP,
        },
        "breakout": {
            "vol_ratio": BREAKOUT_VOL_RATIO,
            "chg_min": BREAKOUT_CHG_MIN,
            "chg_max": BREAKOUT_CHG_MAX,
            "vs_recent_vol_ratio": BREAKOUT_VS_RECENT_VOL_RATIO,
            "require_hold_above_box": True,
            "require_ma_bull": True,
            # v2（2026-08-16）：防假突破/底部震荡
            "max_pullbacks_after_breakout": 0,   # 突破后跌破箱体上沿允许次数（strict）
            "require_ma60": True,                # 收盘须站上 MA60
        },
        "fund_filter": {
            "days": FUND_FLOW_DAYS,
            "min_ratio": FUND_FLOW_MIN_RATIO,
        },
        "fundamental_filter": {
            "min_price": MIN_PRICE,
            "pe": [MIN_PE, MAX_PE],
            "pb": [MIN_PB, MAX_PB],
            "mv_yi": [MIN_MV_YI, MAX_MV_YI],
            "min_list_days": MIN_LIST_DAYS,
        },
        "score_weights": {
            "signal": SIGNAL_SCORE_WEIGHT,
            "fund": FUND_SCORE_WEIGHT,
            "fundamental": FUNDAMENTAL_SCORE_WEIGHT,
        },
        "pool": {
            "a_tier": "strict_only",
            "include_relaxed_in_a": INCLUDE_RELAXED_IN_A,
            "top_n_trade": TOP_N_TRADE,
            "defense_clears_a": True,
        },
        "exit_defaults": {
            "fixed": {
                "stop_pct": STOP_LOSS_PCT,
                "target_pct": TARGET_PCT_1,
                "max_hold": MAX_HOLD_DAYS,
                "priority": "stop_then_target_then_time",
            },
            "bench": {
                "stop_pct": BENCH_STOP_PCT,
                "max_hold": BENCH_MAX_HOLD_DAYS,
                "exit_window": BENCH_EXIT_WINDOW,
                "strong_reset": BENCH_STRONG_RESET,
                "priority": "stop_then_bench_then_time",
            },
        },
        "non_goals": [
            "不得用采样日+1替代突破日+1",
            "Lab 网格不得改变入场 timing",
            "B 池 relaxed / theme_fill 禁止与 A 混排",
            "degraded 研究模式禁止声称 edge / 可下单参数",
        ],
    }


def normalize_breakout_date(value: Any) -> str:
    """统一为 YYYYMMDD；无效返回空串。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def signal_bar_index(bars: pd.DataFrame, breakout_date: Any) -> int | None:
    """突破日在 bars 中的**位置**索引（iloc 用）；bars 须含 trade_date 且按时间排序。"""
    bd = normalize_breakout_date(breakout_date)
    if not bd or bars is None or bars.empty:
        return None
    dates = [
        normalize_breakout_date(v)
        for v in bars["trade_date"].tolist()
    ]
    try:
        return dates.index(bd)
    except ValueError:
        return None


def entry_bar_index(signal_i: int, n_bars: int) -> int | None:
    """信号日下一根 K 线索引。"""
    if signal_i is None or signal_i < 0:
        return None
    nxt = signal_i + 1
    if nxt >= n_bars:
        return None
    return nxt


def resolve_entry_from_signal(
    bars: pd.DataFrame,
    signal: dict[str, Any],
    *,
    require_breakout: bool = True,
) -> dict[str, Any]:
    """从 detect 结果解析入场位置。

    返回:
      ok, signal_index, entry_index, breakout_date, entry_timing, reason
    """
    if require_breakout and not signal.get("is_breakout"):
        return {
            "ok": False,
            "signal_index": None,
            "entry_index": None,
            "breakout_date": "",
            "entry_timing": ENTRY_TIMING,
            "reason": "not_breakout",
        }
    bd = normalize_breakout_date(signal.get("breakout_date"))
    sig_i = signal_bar_index(bars, bd)
    if sig_i is None:
        return {
            "ok": False,
            "signal_index": None,
            "entry_index": None,
            "breakout_date": bd,
            "entry_timing": ENTRY_TIMING,
            "reason": "breakout_date_not_in_bars",
        }
    ent_i = entry_bar_index(sig_i, len(bars))
    if ent_i is None:
        return {
            "ok": False,
            "signal_index": sig_i,
            "entry_index": None,
            "breakout_date": bd,
            "entry_timing": ENTRY_TIMING,
            "reason": "no_next_bar",
        }
    return {
        "ok": True,
        "signal_index": sig_i,
        "entry_index": ent_i,
        "breakout_date": bd,
        "entry_timing": ENTRY_TIMING,
        "reason": "ok",
    }


def entry_price_from_bars(bars: pd.DataFrame, signal_index: int) -> float | None:
    """信号日下一交易日开盘价；无 open 用 close。signal_index 为信号日索引。"""
    ent_i = entry_bar_index(signal_index, len(bars))
    if ent_i is None:
        return None
    row = bars.iloc[ent_i]
    op = row.get("open")
    if op is not None and not pd.isna(op) and float(op) > 0:
        return float(op)
    cl = row.get("close")
    if cl is not None and not pd.isna(cl) and float(cl) > 0:
        return float(cl)
    return None


def breakout_in_recent_window(
    breakout_date: Any,
    sample_day: str,
    calendar: list[str],
    *,
    window: int = BREAKOUT_WINDOW_DAYS,
) -> bool:
    """突破日是否落在 sample_day 往前 window 个交易日（含当日）。"""
    bd = normalize_breakout_date(breakout_date)
    day = normalize_breakout_date(sample_day)
    if not bd or not day or not calendar:
        return False
    cal = [normalize_breakout_date(d) for d in calendar]
    try:
        day_i = cal.index(day)
    except ValueError:
        # 日历可能比 bars 稀，用字典近似
        cal_index = {d: i for i, d in enumerate(cal)}
        day_i = cal_index.get(day, -1)
        if day_i < 0:
            return False
    recent = set(cal[max(0, day_i - window): day_i + 1])
    return bd in recent


def is_a_pool_signal(signal: dict[str, Any]) -> bool:
    """形态层是否构成 A 池候选（不含资金/基本面/环境过滤）。"""
    return bool(signal.get("is_breakout"))


def assert_entry_aligned(simulation: dict[str, Any], expected_entry_index: int) -> None:
    """调试用：模拟结果 entry_index 必须与定义一致。"""
    got = simulation.get("entry_index")
    if got is not None and int(got) != int(expected_entry_index):
        raise AssertionError(
            f"entry_index 与 ENTRY_DEFINITION 不一致: got={got} expected={expected_entry_index}"
        )
