"""
横盘吸筹 → 启动 信号引擎
========================
专业箱体（吸筹平台）判定：

阶段1 箱体识别（在突破日之前的平台上）：
  - 时长 ∈ [BOX_MIN_DAYS, BOX_MAX_DAYS]（约 1~6 个月）
  - 稳健振幅：去极值后 (阻力-支撑)/中轴 ≤ BOX_MAX_AMP
  - 趋势平坦：收盘线性斜率 + 前/后半段漂移双检
  - 结构完整：支撑/压力各至少 N 次触及；收盘落在箱体中部有足够占比
  - 震荡而非单边：至少 1 次有效高低切换（swing）
  - 箱体右端锚定在突破日之前（不把突破 K 线算进箱顶）

阶段2 启动确认（最近 BREAKOUT_WINDOW_DAYS 天）：
  - 收盘有效突破阻力（非仅上影刺破）
  - 放量 ≥ BREAKOUT_VOL_RATIO × 箱体均量
  - 涨幅适中；突破后收盘仍站在阻力上方
  - 均线多头：收盘>MA20 且 MA5>MA20

阶段3 资金流确认（scoring 层）
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from config import (
    BOX_MAX_AMP,
    BOX_MAX_DAYS,
    BOX_MAX_MID_DRAWDOWN,
    BOX_MAX_MID_DRAWDOWN as _STRICT_MID_DD,
    BOX_MIN_DAYS,
    BOX_POS_LOOKBACK,
    BOX_POS_TREND_LOOKBACK,
    BOX_POS_TREND_MAX_DROP,
    BREAKOUT_CHG_MAX,
    BREAKOUT_CHG_MIN,
    BREAKOUT_VOL_RATIO,
    BREAKOUT_VS_RECENT_VOL_RATIO,
    TREND_SLOPE_LIMIT,
    VOL_SHRINK_RATIO,
)

# 突破确认窗口（最近 N 天内发生放量突破都算“启动”）
BREAKOUT_WINDOW_DAYS = 5

# ── 箱体结构参数（专业形态）──
BOX_EDGE_FRAC = 0.18          # 触及带：相对箱体高度的比例
BOX_MIN_SUP_TOUCHES = 2       # 支撑最少触及次数
BOX_MIN_RES_TOUCHES = 2       # 压力最少触及次数
BOX_MID_OCCUPANCY_MIN = 0.28  # 收盘落在中部 50% 区间的最低占比
BOX_MIN_SWINGS = 1            # 最少有效摆动次数（高低切换）
BOX_HALF_DRIFT_MAX = 0.08     # 前/后半段收盘中位漂移 / 中轴 上限
BOX_CLOSE_AMP_MAX_RATIO = 1.05  # 收盘振幅相对稳健振幅可略宽
BOX_OUTLIER_TRIM = True       # 去影线极值


def _linreg_slope(values: np.ndarray) -> float:
    """收盘价序列的线性回归斜率（相对均值归一化，日度）。"""
    if len(values) < 5:
        return float("inf")
    x = np.arange(len(values), dtype=float)
    y = values.astype(float)
    mean_x, mean_y = x.mean(), y.mean()
    denom = ((x - mean_x) ** 2).sum()
    if denom == 0 or mean_y == 0:
        return float("inf")
    slope = ((x - mean_x) * (y - mean_y)).sum() / denom
    return float(slope / max(abs(mean_y), 1e-9))


def _linreg_r2(values: np.ndarray) -> float:
    """线性趋势解释度 R²；箱体应偏低（非单边通道）。"""
    if len(values) < 5:
        return 1.0
    y = values.astype(float)
    x = np.arange(len(y), dtype=float)
    mean_y = y.mean()
    ss_tot = ((y - mean_y) ** 2).sum()
    if ss_tot <= 1e-12:
        return 0.0
    # 拟合
    mean_x = x.mean()
    denom = ((x - mean_x) ** 2).sum()
    if denom <= 0:
        return 1.0
    b = ((x - mean_x) * (y - mean_y)).sum() / denom
    a = mean_y - b * mean_x
    y_hat = a + b * x
    ss_res = ((y - y_hat) ** 2).sum()
    return float(max(0.0, min(1.0, 1.0 - ss_res / ss_tot)))


def _robust_support_resistance(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
) -> tuple[float, float, float]:
    """稳健支撑/阻力。

    专业处理：
      - 默认用 high/low 的分位去极值（避免一根长影线撑破箱体）
      - 短窗口样本少时退回 max/min，但剔除最极端 1 根（若偏离中位过大）
    返回 (resistance, support, mid)
    """
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    n = len(c)
    if n < 5:
        res, sup = float(np.nanmax(h)), float(np.nanmin(l))
        mid = (res + sup) / 2.0 if res > sup else float(np.nanmean(c))
        return res, sup, mid

    if BOX_OUTLIER_TRIM and n >= 30:
        # 长平台：用 92/8 分位作结构边界，比裸 max/min 稳
        res = float(np.nanpercentile(h, 92))
        sup = float(np.nanpercentile(l, 8))
        # 但真实箱顶/箱底常贴近局部峰谷；用「分位与次极值」折中
        h_sorted = np.sort(h[~np.isnan(h)])
        l_sorted = np.sort(l[~np.isnan(l)])
        if len(h_sorted) >= 3:
            # 阻力：次高与 92 分位取较高者中偏保守 → 取 max(次高*0.3+最高*0.0, p92)
            # 实务：阻力取「去掉最高影线后的最高」与 p92 的较大者（保证可突破）
            res_trim = float(h_sorted[-2])  # 去掉单日最高影
            res = max(res, min(res_trim, float(h_sorted[-1])))
            res = float(np.median([res, res_trim, float(np.nanpercentile(h, 90))]))
        if len(l_sorted) >= 3:
            sup_trim = float(l_sorted[1])  # 去掉单日最低影
            sup = float(np.median([sup, sup_trim, float(np.nanpercentile(l, 10))]))
    elif BOX_OUTLIER_TRIM and n >= 12:
        h_sorted = np.sort(h[~np.isnan(h)])
        l_sorted = np.sort(l[~np.isnan(l)])
        res = float(h_sorted[-2]) if len(h_sorted) >= 2 else float(h_sorted[-1])
        sup = float(l_sorted[1]) if len(l_sorted) >= 2 else float(l_sorted[0])
        # 若次极值与极值接近，用极值（真箱顶）
        if len(h_sorted) >= 2 and h_sorted[-1] <= h_sorted[-2] * 1.015:
            res = float(h_sorted[-1])
        if len(l_sorted) >= 2 and l_sorted[0] >= l_sorted[1] * 0.985:
            sup = float(l_sorted[0])
    else:
        res = float(np.nanmax(h))
        sup = float(np.nanmin(l))

    if not np.isfinite(res) or not np.isfinite(sup) or res <= 0 or sup <= 0:
        res = float(np.nanmax(c))
        sup = float(np.nanmin(c))
    if res < sup:
        res, sup = sup, res
    mid = (res + sup) / 2.0
    # 中轴用收盘中位微调，避免阻力/支撑被影线轻微偏移
    c_med = float(np.nanmedian(c))
    if abs(c_med - mid) / max(mid, 1e-9) < 0.05:
        mid = 0.6 * mid + 0.4 * c_med
    return res, sup, mid


def _count_boundary_touches(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    resistance: float,
    support: float,
    edge_frac: float = BOX_EDGE_FRAC,
) -> tuple[int, int, float]:
    """统计支撑/压力触及次数 + 中部占用率。

    触及定义：当日 low 进入支撑带 或 high 进入压力带（带宽=箱高*edge_frac）。
    """
    height = max(resistance - support, 1e-9)
    band = height * edge_frac
    sup_zone_hi = support + band
    res_zone_lo = resistance - band

    sup_touches = 0
    res_touches = 0
    # 防连板重复计数：同向触及至少隔 2 根
    last_sup_i = -99
    last_res_i = -99
    for i in range(len(closes)):
        lo = float(lows[i])
        hi = float(highs[i])
        if lo <= sup_zone_hi and lo >= support - band * 0.5:
            if i - last_sup_i >= 2:
                sup_touches += 1
                last_sup_i = i
        if hi >= res_zone_lo and hi <= resistance + band * 0.5:
            if i - last_res_i >= 2:
                res_touches += 1
                last_res_i = i

    # 中部 25%~75% 区间占用
    lo_m = support + 0.25 * height
    hi_m = support + 0.75 * height
    mid_hits = np.sum((closes >= lo_m) & (closes <= hi_m))
    mid_frac = float(mid_hits / max(len(closes), 1))
    return int(sup_touches), int(res_touches), mid_frac


def _count_swings(closes: np.ndarray, min_move_frac: float = 0.015) -> int:
    """有效摆动次数：相对中轴至少 min_move_frac 的方向切换次数。"""
    if len(closes) < 8:
        return 0
    c = closes.astype(float)
    mid = float(np.nanmedian(c))
    thr = max(abs(mid) * min_move_frac, 1e-6)
    # 平滑：3 日均，减少噪声摆动
    if len(c) >= 5:
        ker = np.ones(3) / 3.0
        sm = np.convolve(c, ker, mode="same")
    else:
        sm = c
    swings = 0
    direction = 0  # 1 up, -1 down
    anchor = sm[0]
    for x in sm[1:]:
        if direction >= 0 and x < anchor - thr:
            if direction == 1:
                swings += 1
            direction = -1
            anchor = x
        elif direction <= 0 and x > anchor + thr:
            if direction == -1:
                swings += 1
            direction = 1
            anchor = x
        else:
            if direction >= 0 and x > anchor:
                anchor = x
            if direction <= 0 and x < anchor:
                anchor = x
    return int(swings)


def _half_drift(closes: np.ndarray) -> float:
    """前半/后半收盘中位漂移（相对中轴）。"""
    n = len(closes)
    if n < 8:
        return 0.0
    half = n // 2
    a = float(np.nanmedian(closes[:half]))
    b = float(np.nanmedian(closes[half:]))
    mid = float(np.nanmedian(closes))
    if mid <= 0:
        return 0.0
    return abs(b - a) / mid


def evaluate_box_window(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    vols: np.ndarray | None = None,
    *,
    box_max_amp: float = BOX_MAX_AMP,
    slope_limit: float = TREND_SLOPE_LIMIT,
    require_structure: bool = True,
) -> dict[str, Any]:
    """评估一段 K 线是否构成合格吸筹箱体。返回 metrics + ok 标志。"""
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    n = len(c)
    out: dict[str, Any] = {
        "ok": False,
        "resistance": None,
        "support": None,
        "mid": None,
        "amp": None,
        "close_amp": None,
        "slope": None,
        "r2": None,
        "sup_touches": 0,
        "res_touches": 0,
        "mid_frac": 0.0,
        "swings": 0,
        "half_drift": 0.0,
        "vol_shrink": None,
        "quality": 0.0,  # 越大越好
        "fail": [],
    }
    if n < 8:
        out["fail"].append("窗口过短")
        return out

    res, sup, mid = _robust_support_resistance(h, l, c)
    height = res - sup
    if height <= 0 or mid <= 0:
        out["fail"].append("边界无效")
        return out

    amp = height / mid  # 用中轴归一，比 /min 更稳
    # 兼容旧定义展示：也算 (hi-lo)/lo
    amp_lo = height / max(sup, 1e-9)
    c_max, c_min = float(np.nanmax(c)), float(np.nanmin(c))
    close_amp = (c_max - c_min) / max(c_min, 1e-9)

    slope = _linreg_slope(c)
    r2 = _linreg_r2(c)
    sup_t, res_t, mid_frac = _count_boundary_touches(h, l, c, res, sup)
    swings = _count_swings(c)
    drift = _half_drift(c)

    vol_shrink = None
    if vols is not None and len(vols) == n:
        half = max(1, n // 2)
        fv = float(np.nanmean(vols[:half]))
        bv = float(np.nanmean(vols[half:]))
        vol_shrink = (bv / fv) if fv > 0 else None

    # 时长自适应振幅：越长允许略宽（√T），但封顶
    length_scale = min(1.25, 1.0 + 0.12 * math.log(max(n, 20) / 20.0))
    amp_limit = box_max_amp * length_scale

    fails: list[str] = []
    if amp > amp_limit and amp_lo > amp_limit * 1.05:
        fails.append(f"振幅{amp:.1%}>{amp_limit:.0%}")
    if abs(slope) > slope_limit:
        fails.append(f"斜率{slope:.4f}过陡")
    if drift > BOX_HALF_DRIFT_MAX:
        fails.append(f"前后漂移{drift:.1%}过大")
    # 强趋势通道：斜率尚可但 R² 很高 → 拒绝
    if r2 >= 0.72 and abs(slope) > slope_limit * 0.45:
        fails.append(f"单边通道R²={r2:.2f}")
    if close_amp > amp_limit * BOX_CLOSE_AMP_MAX_RATIO * 1.15:
        fails.append(f"收盘振幅{close_amp:.1%}过大")

    if require_structure:
        if sup_t < BOX_MIN_SUP_TOUCHES:
            fails.append(f"支撑触及{sup_t}<{BOX_MIN_SUP_TOUCHES}")
        if res_t < BOX_MIN_RES_TOUCHES:
            fails.append(f"压力触及{res_t}<{BOX_MIN_RES_TOUCHES}")
        if mid_frac < BOX_MID_OCCUPANCY_MIN:
            fails.append(f"中部占用{mid_frac:.0%}<{BOX_MID_OCCUPANCY_MIN:.0%}")
        if swings < BOX_MIN_SWINGS:
            fails.append(f"摆动{swings}<{BOX_MIN_SWINGS}")

    # 质量分（越大越好）：窄振幅、多触及、多摆动、低漂移、低 R²、适度缩量
    q = 0.0
    q += max(0.0, 1.0 - amp / max(amp_limit, 1e-6)) * 35.0
    q += min(sup_t, 6) * 3.0 + min(res_t, 6) * 3.0
    q += min(swings, 8) * 2.5
    q += max(0.0, 1.0 - abs(slope) / max(slope_limit, 1e-9)) * 12.0
    q += max(0.0, 1.0 - drift / BOX_HALF_DRIFT_MAX) * 8.0
    q += max(0.0, 1.0 - r2) * 8.0
    q += max(0.0, mid_frac - 0.2) * 20.0
    # 时长奖励（对数）
    q += min(12.0, math.log(max(n, 15) / 15.0) * 6.0)
    if vol_shrink is not None and vol_shrink <= VOL_SHRINK_RATIO:
        q += max(0.0, (1.0 - vol_shrink) * 10.0)

    out.update({
        "ok": len(fails) == 0,
        "resistance": res,
        "support": sup,
        "mid": mid,
        "amp": float(amp),
        "amp_lo": float(amp_lo),
        "close_amp": float(close_amp),
        "slope": float(slope),
        "r2": float(r2),
        "sup_touches": sup_t,
        "res_touches": res_t,
        "mid_frac": float(mid_frac),
        "swings": swings,
        "half_drift": float(drift),
        "vol_shrink": None if vol_shrink is None else float(vol_shrink),
        "quality": float(q),
        "fail": fails,
        "amp_limit": float(amp_limit),
    })
    return out


def _find_best_box(
    obs: pd.DataFrame,
    *,
    box_min_days: int,
    box_max_days: int,
    box_max_amp: float,
    breakout_window_days: int,
    require_structure: bool = True,
) -> dict[str, Any] | None:
    """在观察窗内搜索最佳箱体。

    关键约束：
      1) 箱体右端必须落在「突破窗口左沿」附近（为突破日留空）
      2) 只接受 evaluate_box_window 合格的窗口
      3) 质量分 + 时长 + 贴近性 综合最优
    """
    n = len(obs)
    if n < box_min_days + 2:
        return None

    # 箱体右端：至少留给突破窗口 1 根，最多在 last-1
    # 优先：end 落在 [n - breakout_window_days - 1, n - 2]
    end_lo = max(box_min_days - 1, n - breakout_window_days - 1)
    end_hi = n - 2
    if end_hi < box_min_days - 1:
        return None
    end_lo = min(end_lo, end_hi)

    h_all = obs["_h"].to_numpy(dtype=float)
    l_all = obs["_l"].to_numpy(dtype=float)
    c_all = obs["_c"].to_numpy(dtype=float)
    v_all = obs["_v"].to_numpy(dtype=float)

    best: dict[str, Any] | None = None
    best_score = -1e18

    # 右端从近到远；长度从长到短偏好在 score 里体现
    for end in range(end_hi, end_lo - 1, -1):
        max_len_here = min(box_max_days, end + 1)
        if max_len_here < box_min_days:
            continue
        # 步长：长箱稀疏、短箱加密
        lengths = list(range(box_min_days, max_len_here + 1))
        # 优先采样：先长后短（质量相近时长赢）
        for length in reversed(lengths):
            step = 1 if length <= 45 else (2 if length <= 90 else 3)
            # 该 end 固定时 start 唯一：start = end - length + 1
            start = end - length + 1
            if start < 0:
                continue
            # 仅当 end 在优先区时 step 用于多 end 扫描；此处 end 已逐点
            if length > 45 and (end % step) != 0 and end != end_hi:
                continue

            m = evaluate_box_window(
                h_all[start: end + 1],
                l_all[start: end + 1],
                c_all[start: end + 1],
                v_all[start: end + 1],
                box_max_amp=box_max_amp,
                require_structure=require_structure,
            )
            if not m["ok"]:
                continue

            # 贴近性：箱体右端越靠近最新越好
            recency_gap = (n - 1) - end  # 0 最佳（紧贴最新前一根）
            recency_pen = recency_gap * 1.8
            # 时长偏好：优先更长横盘（6 个月平台 > 短窗子区间）
            # length 20→0, 60→+11, 125→+18
            length_bonus = 8.0 * math.log(max(length, box_min_days) / float(box_min_days))
            # 综合分（越大越好）
            score = float(m["quality"]) + length_bonus - recency_pen
            if score > best_score:
                best_score = score
                best = {
                    "start": start,
                    "end": end,
                    "length": length,
                    "metrics": m,
                    "score": score,
                }

    # 若严格结构无结果：放宽结构再扫一轮（仅振幅+平坦+漂移），标记 soft_structure
    if best is None and require_structure:
        return _find_best_box(
            obs,
            box_min_days=box_min_days,
            box_max_days=box_max_days,
            box_max_amp=box_max_amp * 1.05,
            breakout_window_days=breakout_window_days,
            require_structure=False,
        )

    return best


def detect_accumulation_breakout(
    df: pd.DataFrame,
    box_max_days: int | None = None,
    box_min_days: int | None = None,
    box_max_amp: float | None = None,
    breakout_vol_ratio: float | None = None,
    breakout_chg_min: float | None = None,
    breakout_chg_max: float | None = None,
    breakout_window_days: int | None = None,
    require_structure: bool = True,
    box_max_mid_drawdown: float | None = None,
    pos_trend_max_drop: float | None = None,
    breakout_vs_recent_vol_ratio: float | None = None,
) -> dict:
    """对单只股票 K 线做横盘吸筹 + 启动检测。

    df 列：date, open, high, low, close, vol（或 volume），按 date 升序。
    require_structure=False：放宽结构（B 池 relaxed），仍要求振幅/平坦/突破。
    box_max_mid_drawdown：箱体中轴相对窗口前段高点的最大回撤（防下跌中继）。
    pos_trend_max_drop：近 BOX_POS_TREND_LOOKBACK 日涨跌幅下限（防大趋势下跌）。
    breakout_vs_recent_vol_ratio：突破日量 / 前5日均量 下限（放量双重确认）。
    """
    box_max_days = box_max_days or BOX_MAX_DAYS
    box_min_days = box_min_days or BOX_MIN_DAYS
    box_max_amp = box_max_amp if box_max_amp is not None else BOX_MAX_AMP
    breakout_vol_ratio = breakout_vol_ratio or BREAKOUT_VOL_RATIO
    breakout_chg_min = breakout_chg_min if breakout_chg_min is not None else BREAKOUT_CHG_MIN
    breakout_chg_max = breakout_chg_max if breakout_chg_max is not None else BREAKOUT_CHG_MAX
    breakout_window_days = breakout_window_days or BREAKOUT_WINDOW_DAYS
    box_max_mid_drawdown = (
        BOX_MAX_MID_DRAWDOWN if box_max_mid_drawdown is None else box_max_mid_drawdown
    )
    pos_trend_max_drop = (
        BOX_POS_TREND_MAX_DROP if pos_trend_max_drop is None else pos_trend_max_drop
    )
    breakout_vs_recent_vol_ratio = (
        BREAKOUT_VS_RECENT_VOL_RATIO
        if breakout_vs_recent_vol_ratio is None
        else breakout_vs_recent_vol_ratio
    )

    result: dict[str, Any] = {
        "is_breakout": False,
        "box_start_idx": None,
        "box_end_idx": None,
        "box_high": None,
        "box_low": None,
        "box_days": 0,
        "box_amp": None,
        "breakout_date": None,
        "breakout_vol_ratio": None,
        "breakout_pct_chg": None,
        "vol_shrink_ratio": None,
        "ma5": None,
        "ma10": None,
        "ma20": None,
        "reasons": [],
    }

    need_bars = box_min_days + 5
    if df is None or len(df) < need_bars:
        result["reasons"].append("K线长度不足")
        return result

    df = df.copy()
    vol_col = "vol" if "vol" in df.columns else "volume"
    if vol_col not in df.columns:
        result["reasons"].append("缺少成交量列")
        return result
    df["_v"] = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)
    df["_c"] = pd.to_numeric(df["close"], errors="coerce")
    df["_h"] = pd.to_numeric(df["high"], errors="coerce")
    df["_l"] = pd.to_numeric(df["low"], errors="coerce")
    df = df.dropna(subset=["_c"]).reset_index(drop=True)
    if len(df) < need_bars:
        result["reasons"].append("清洗后K线不足")
        return result

    ma5 = df["_c"].rolling(5).mean().iloc[-1]
    ma10 = df["_c"].rolling(10).mean().iloc[-1]
    ma20 = df["_c"].rolling(20).mean().iloc[-1]
    result["ma5"] = None if pd.isna(ma5) else float(ma5)
    result["ma10"] = None if pd.isna(ma10) else float(ma10)
    result["ma20"] = None if pd.isna(ma20) else float(ma20)

    # 观察窗：最长箱体 + 突破窗口 + 余量
    obs_len = min(len(df), box_max_days + breakout_window_days + 5)
    obs = df.tail(obs_len).reset_index(drop=True)
    if len(obs) < box_min_days + 2:
        result["reasons"].append("观察窗口不足")
        return result

    best = _find_best_box(
        obs,
        box_min_days=box_min_days,
        box_max_days=box_max_days,
        box_max_amp=box_max_amp,
        breakout_window_days=breakout_window_days,
        require_structure=require_structure,
    )
    if best is None:
        result["reasons"].append("未找到合格横盘箱体")
        return result

    m = best["metrics"]
    box = obs.iloc[best["start"]: best["end"] + 1]
    box_high = float(m["resistance"])
    box_low = float(m["support"])
    box_amp = float(m["amp"])
    slope = float(m["slope"])
    vol_shrink = m.get("vol_shrink")

    # ── 箱体位置约束：中轴相对窗口前段高点的回撤（防下跌中继误选） ──
    box_mid = (box_high + box_low) / 2.0
    pre_end = max(1, best["start"])  # 箱体开始之前的段落
    pre_seg = obs.iloc[:pre_end].tail(BOX_POS_LOOKBACK)
    pre_high = None
    if len(pre_seg) >= 10:
        _ph = pd.to_numeric(pre_seg["high"], errors="coerce")
        pre_high = float(_ph.max()) if _ph.notna().any() else None
    mid_drawdown = (box_mid / pre_high - 1.0) if (pre_high and pre_high > 0) else 0.0

    # ── 大趋势约束：近 BOX_POS_TREND_LOOKBACK 日涨跌幅（防大趋势下跌） ──
    _closes = pd.to_numeric(obs["_c"], errors="coerce").dropna()
    trend_ret = (
        float(_closes.iloc[-1] / _closes.iloc[-BOX_POS_TREND_LOOKBACK] - 1.0)
        if len(_closes) >= BOX_POS_TREND_LOOKBACK
        else 0.0
    )

    last = obs.iloc[-1]
    last_close = float(last["_c"])
    last_vol = float(last["_v"])
    box_avg_vol = float(box["_v"].mean()) if len(box) else 0.0

    # ── 突破：仅在箱体结束之后的窗口内寻找 ──
    # 突破日必须 > box end，且落在最近 breakout_window_days
    search_start = best["end"] + 1
    search_end = len(obs) - 1
    win_left = max(search_start, len(obs) - breakout_window_days)
    breakout_found = None
    if win_left <= search_end:
        for i in range(search_end, win_left - 1, -1):
            row = obs.iloc[i]
            rc = float(row["_c"])
            rv = float(row["_v"])
            # 收盘有效突破阻力（允许 0.1% 浮点误差）
            if rc <= box_high * 1.001:
                continue
            if box_avg_vol <= 0 or rv < breakout_vol_ratio * box_avg_vol:
                continue
            # 前收
            if i <= 0:
                continue
            prev_close = float(obs.iloc[i - 1]["_c"])
            if prev_close <= 0:
                continue
            chg = rc / prev_close - 1.0
            if not (breakout_chg_min <= chg <= breakout_chg_max):
                continue
            # 突破日最高价应至少触及/越过阻力（避免仅缺口定义争议；收盘已过即可）
            breakout_found = {
                "date": str(row["date"]),
                "close": rc,
                "vol": rv,
                "vol_ratio": rv / box_avg_vol if box_avg_vol > 0 else 0.0,
                "pct_chg": chg,
                "pos_in_obs": int(i),
            }
            break

    # ── 突破日量 / 前5日均量（放量双重确认，防箱体缩量稀释分母虚高） ──
    recent_vol_ratio = None
    if breakout_found:
        _i = breakout_found["pos_in_obs"]
        _pre5 = obs.iloc[max(0, _i - 5): _i]
        if len(_pre5) > 0:
            _p5v = float(pd.to_numeric(_pre5["_v"], errors="coerce").mean())
            if _p5v > 0:
                recent_vol_ratio = float(breakout_found["vol"] / _p5v)

    cond_box = box_amp <= (m.get("amp_limit") or box_max_amp) * 1.01
    cond_flat = abs(slope) <= TREND_SLOPE_LIMIT * 1.05
    if require_structure:
        cond_structure = (
            m.get("sup_touches", 0) >= BOX_MIN_SUP_TOUCHES
            and m.get("res_touches", 0) >= BOX_MIN_RES_TOUCHES
            and m.get("swings", 0) >= BOX_MIN_SWINGS
        )
    else:
        # relaxed：至少有一定边界互动或摆动，避免完全无结构
        cond_structure = (
            (m.get("sup_touches", 0) + m.get("res_touches", 0) >= 2)
            or m.get("swings", 0) >= 1
            or m.get("mid_frac", 0) >= 0.2
        )
    cond_break = breakout_found is not None
    cond_hold = last_close > box_high
    cond_ma = (
        result["ma5"] is not None
        and result["ma20"] is not None
        and last_close > result["ma20"]
        and result["ma5"] > result["ma20"]
    )
    cond_position = mid_drawdown >= -box_max_mid_drawdown
    cond_trend = trend_ret >= pos_trend_max_drop
    cond_recent_vol = recent_vol_ratio is None or recent_vol_ratio >= breakout_vs_recent_vol_ratio

    bf = breakout_found or {}
    vol_ratio = bf.get("vol_ratio")
    pct_chg = bf.get("pct_chg")

    result.update({
        "box_start_idx": int(best["start"]),
        "box_end_idx": int(best["end"]),
        "box_high": box_high,
        "box_low": box_low,
        "box_days": int(best["length"]),
        "box_amp": float(box_amp),
        "box_slope": float(slope),
        "box_r2": float(m.get("r2") or 0),
        "box_quality": float(m.get("quality") or 0),
        "sup_touches": int(m.get("sup_touches") or 0),
        "res_touches": int(m.get("res_touches") or 0),
        "box_swings": int(m.get("swings") or 0),
        "box_mid_frac": float(m.get("mid_frac") or 0),
        "half_drift": float(m.get("half_drift") or 0),
        "breakout_date": bf.get("date"),
        "breakout_vol_ratio": float(vol_ratio) if vol_ratio is not None else None,
        "breakout_pct_chg": float(pct_chg) if pct_chg is not None else None,
        "vol_shrink_ratio": None if vol_shrink is None else float(vol_shrink),
        "latest_close": last_close,
        "latest_vol": last_vol,
        "box_avg_vol": box_avg_vol,
        "pre_high": pre_high,
        "mid_drawdown": float(mid_drawdown),
        "trend_ret": float(trend_ret),
        "recent_vol_ratio": None if recent_vol_ratio is None else float(recent_vol_ratio),
        "cond_box": bool(cond_box),
        "cond_flat": bool(cond_flat),
        "cond_structure": bool(cond_structure),
        "cond_shrink": bool(vol_shrink is not None and vol_shrink <= VOL_SHRINK_RATIO),
        "cond_break": bool(cond_break),
        "cond_hold": bool(cond_hold),
        "cond_ma": bool(cond_ma),
        "cond_position": bool(cond_position),
        "cond_trend": bool(cond_trend),
        "cond_recent_vol": bool(cond_recent_vol),
    })

    failures: list[str] = []
    if not cond_box:
        failures.append(f"振幅{box_amp:.1%}超限")
    if not cond_flat:
        failures.append(f"斜率{slope:.4f}过陡")
    if not cond_structure:
        failures.append(
            f"结构不足(支撑{m.get('sup_touches')}/压力{m.get('res_touches')}/摆动{m.get('swings')})"
        )
    if not cond_break:
        failures.append(f"窗口内未放量突破阻力({last_close:.2f} vs {box_high:.2f})")
    if not cond_hold:
        failures.append(f"已跌回箱体({last_close:.2f}<{box_high:.2f})")
    if not cond_ma:
        failures.append("均线未多头")
    if not cond_position:
        failures.append(f"箱体位置过深(中轴回撤{mid_drawdown:+.0%}，下跌中继，非吸筹平台)")
    if not cond_trend:
        failures.append(f"大趋势下跌(近{BOX_POS_TREND_LOOKBACK}日{trend_ret:+.0%})")
    if not cond_recent_vol:
        failures.append(f"突破日量/前5日均量{recent_vol_ratio:.1f}倍<{breakout_vs_recent_vol_ratio}(放量不足)")

    # 结构在 soft 回退时可能不足：strict 命中仍要求结构；若 metrics.ok 来自 soft 则已在 fail 中
    # 对 is_breakout：结构作为硬条件（专业形态）
    if failures:
        result["reasons"] = failures
        # 附加诊断
        if m.get("fail"):
            result["box_fail_detail"] = list(m["fail"])
    else:
        result["is_breakout"] = True
        result["reasons"] = [
            f"横盘{int(best['length'])}日振幅{box_amp:.1%}"
            f"(支撑触{m.get('sup_touches')}压力触{m.get('res_touches')})",
            f"{bf.get('date', '')}放量{vol_ratio:.1f}倍突破" if vol_ratio else "放量突破",
            f"涨幅{pct_chg:.1%}" if pct_chg is not None else "",
        ]

    return result


def score_breakout_strength(sig: dict) -> float:
    """信号强度分（0-100）。

    排序原则：
      1) 横盘越长越高
      2) 信号越明确越高（放量、窄箱、结构、缩量、站稳、均线）
    """
    if not sig.get("is_breakout"):
        return 0.0

    s = 0.0
    days = float(sig.get("box_days") or 0)
    if days > 0:
        base = max(0.0, math.log(max(days, 15) / 15.0))
        s += min(26.0, base * 13.0)
        if 40 <= days <= 90:
            s += 4.0
        elif 90 < days <= 125:
            s += 3.0
        elif 30 <= days < 40:
            s += 1.5

    vr = float(sig.get("breakout_vol_ratio") or 0)
    if vr > 0:
        s += min(24.0, 8.0 + max(0.0, vr - 1.4) * 11.0)

    amp = float(sig.get("box_amp") or 0)
    if amp <= 0:
        s += 8.0
    else:
        s += max(0.0, min(16.0, 16.0 * (1.0 - amp / 0.28)))

    # 结构质量（触及+摆动）
    sup_t = int(sig.get("sup_touches") or 0)
    res_t = int(sig.get("res_touches") or 0)
    swings = int(sig.get("box_swings") or 0)
    s += min(6.0, sup_t * 1.2) + min(6.0, res_t * 1.2)
    s += min(4.0, swings * 1.0)
    bq = sig.get("box_quality")
    if bq is not None:
        try:
            s += min(6.0, float(bq) / 20.0)
        except (TypeError, ValueError):
            pass

    vs = sig.get("vol_shrink_ratio")
    if vs is not None:
        try:
            vs_f = float(vs)
            s += max(0.0, min(8.0, (1.0 - vs_f) / 0.5 * 8.0))
        except (TypeError, ValueError):
            pass

    if sig.get("cond_hold"):
        s += 3.0
    if sig.get("cond_ma"):
        s += 3.0

    chg = sig.get("breakout_pct_chg")
    if chg is not None:
        try:
            c = float(chg)
            if 0.02 <= c <= 0.05:
                s += 5.0
            elif 0.05 < c <= 0.07:
                s += 3.0
            elif 0.07 < c <= 0.095:
                s += 1.0
        except (TypeError, ValueError):
            pass

    slope = sig.get("box_slope")
    if slope is not None:
        try:
            sl = abs(float(slope))
            s += max(0.0, min(3.0, 3.0 * (1.0 - sl / 0.004)))
        except (TypeError, ValueError):
            pass

    return round(min(100.0, max(0.0, s)), 1)
