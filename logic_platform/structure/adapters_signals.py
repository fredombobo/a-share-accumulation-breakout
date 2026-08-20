"""signals.py 适配层（唯一箱体计算留在 signals，本层只映射不重算）。

map_signal_to_state：把 detect_accumulation_breakout 结果映射为状态机状态。
box_date_range：把 sig 的 obs 内索引映射回 df 的真实日期（供 markArea）。
"""
from __future__ import annotations

import pandas as pd

from config import BOX_MAX_DAYS  # 宿主常量，勿静默修改
from signals import BREAKOUT_WINDOW_DAYS  # 宿主常量（signals.py 模块级）

# 状态枚举（docs §4.2）
STATE_IDLE = "IDLE"
STATE_ACCUMULATION = "ACCUMULATION"
STATE_TIGHTENING = "TIGHTENING"
STATE_BREAKOUT = "BREAKOUT"
STATE_FOLLOW_THROUGH = "FOLLOW_THROUGH"
STATE_FAIL = "FAIL"

STATES = [
    STATE_IDLE, STATE_ACCUMULATION, STATE_TIGHTENING,
    STATE_BREAKOUT, STATE_FOLLOW_THROUGH, STATE_FAIL,
]

# 观测窗（与 signals.detect_accumulation_breakout 内部一致）
_OBS_LEN = BOX_MAX_DAYS + BREAKOUT_WINDOW_DAYS + 5


def box_date_range(
    df: pd.DataFrame, sig: dict
) -> tuple[str | None, str | None]:
    """把 sig 的 box_start_idx/end_idx（obs 内索引）映射回 df 的 date。

    返回 (start_date, end_date)，YYYY-MM-DD；不可用返回 (None, None)。
    """
    if not sig or sig.get("box_start_idx") is None or sig.get("box_end_idx") is None:
        return None, None
    if df is None or df.empty or "date" not in df.columns:
        return None, None
    obs_len = min(len(df), _OBS_LEN)
    obs_start = len(df) - obs_len
    s = obs_start + int(sig["box_start_idx"])
    e = obs_start + int(sig["box_end_idx"])
    if s < 0 or e >= len(df) or s > e:
        return None, None
    return str(df["date"].iloc[s]), str(df["date"].iloc[e])


def _box_invalid(sig: dict) -> bool:
    if not sig:
        return True
    if sig.get("box_days") in (None, 0):
        return True
    reasons = sig.get("reasons") or []
    return any("未找到合格横盘箱体" in r for r in reasons)


def map_signal_to_state(sig: dict) -> tuple[str, list[str]]:
    """把单票 signals 结果映射为 (state, 补充理由[])。

    判定顺序（docs §4.2 状态机）：
      IDLE → ACCUMULATION → (TIGHTENING 子标记) → BREAKOUT
            → FOLLOW_THROUGH / FAIL
    """
    if _box_invalid(sig):
        return STATE_IDLE, ["未找到合格横盘箱体（结构不足）"]

    is_breakout = bool(sig.get("is_breakout"))
    breakout_date = sig.get("breakout_date")
    cond_hold = bool(sig.get("cond_hold", False))
    # sig 本身不带最新日期；由调用方以 as_of 补充说明

    reasons: list[str] = []
    base = sig.get("reasons") or []
    reasons.extend(r for r in base if r not in reasons)

    if is_breakout:
        reasons.insert(0, "收盘突破箱体上沿，量能确认")
        return STATE_BREAKOUT, reasons

    if breakout_date is not None:
        # 曾突破：最新仍站上箱体上沿 → 延续；跌回箱体 → 失败
        if cond_hold:
            reasons.insert(0, "突破后仍站稳箱体上沿（延续）")
            return STATE_FOLLOW_THROUGH, reasons
        reasons.insert(0, "突破后回落至箱体上沿之下（假突破）")
        return STATE_FAIL, reasons

    # 未突破：检查吸筹质量
    box_days = int(sig.get("box_days") or 0)
    cond_box = bool(sig.get("cond_box", False))
    cond_flat = bool(sig.get("cond_flat", False))
    vol_shrink = sig.get("vol_shrink_ratio")
    shrink_ok = vol_shrink is None or vol_shrink <= 0.8

    if box_days >= 20 and cond_box and cond_flat:
        reasons.insert(0, "横盘吸筹：箱体振幅收敛、走势平坦")
        if not shrink_ok:
            reasons.append("注：缩量不足（vol_shrink_ratio 偏高）")
        return STATE_ACCUMULATION, reasons

    reasons.insert(0, "存在箱体但质量条件未全满足（振幅/平坦度）")
    return STATE_IDLE, reasons


def is_tightening(df: pd.DataFrame, box_amp: float | None) -> bool:
    """TIGHTENING 子状态：近 5 日量能 < 前 5 日量能 且 近 5 日振幅收窄。"""
    if df is None or len(df) < 12 or "vol" not in df.columns:
        return False
    vol = pd.to_numeric(df["vol"], errors="coerce")
    recent5 = vol.tail(5).mean()
    prev5 = vol.iloc[-10:-5].mean()
    if pd.isna(recent5) or pd.isna(prev5) or prev5 <= 0:
        return False
    # 近 5 日振幅（high/low 极差 / close 基准）
    hi = pd.to_numeric(df["high"], errors="coerce").tail(5).max()
    lo = pd.to_numeric(df["low"], errors="coerce").tail(5).min()
    ref = pd.to_numeric(df["close"], errors="coerce").iloc[-1]
    if pd.isna(hi) or pd.isna(lo) or pd.isna(ref) or ref <= 0 or not box_amp:
        return False
    recent_amp = (hi - lo) / ref
    return recent5 < prev5 and recent_amp < box_amp
