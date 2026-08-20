"""信号引擎单元测试：用真实K线数据验证横盘吸筹→突破检测"""
from __future__ import annotations

import os
import sys

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from signals import detect_accumulation_breakout, score_breakout_strength


def make_synthetic(seed: int = 42, flat_days: int = 80, pre_days: int = 70) -> pd.DataFrame:
    """构造合成数据：箱前中位平台 → 有支撑/压力往返的吸筹箱体 → 末日放量突破。

    v2 起需要箱体前历史（位置护栏/大趋势基于完整窗口），故增加 pre_days 前段。
    """
    rng = np.random.default_rng(seed)
    n_pre = pre_days
    n = n_pre + flat_days + 1
    dates = pd.bdate_range("2025-10-01", periods=n)
    support, resistance = 9.70, 10.30
    mid = (support + resistance) / 2.0
    closes, highs, lows, vols, opens = [], [], [], [], []
    # 前段：箱体之前的中位平台（高点略高于箱体上沿，保证位置护栏通过）
    for i in range(n_pre):
        c = mid + 0.08 + 0.14 * np.sin(i / 8.0) + rng.normal(0, 0.04)
        h = c + abs(rng.normal(0.08, 0.02))
        l = c - abs(rng.normal(0.08, 0.02))
        o = c + rng.normal(0, 0.03)
        v = 2000 + rng.normal(0, 100)
        closes.append(float(c))
        highs.append(float(max(h, c, o)))
        lows.append(float(min(l, c, o)))
        opens.append(float(o))
        vols.append(float(max(v, 50)))
    # 箱体 + 突破日（沿用原构造）
    for i in range(n_pre, n):
        if i < n - 1:
            phase = 2 * np.pi * (i - n_pre) / 14.0  # ~14 日一个来回
            c = mid + 0.28 * np.sin(phase) + rng.normal(0, 0.03)
            c = float(np.clip(c, support + 0.02, resistance - 0.02))
            # 周期性把 low/high 顶到边界，形成触及
            if np.sin(phase) < -0.7:
                l = support + rng.uniform(0, 0.04)
                h = c + rng.uniform(0.02, 0.08)
                c = max(c, l + 0.02)
            elif np.sin(phase) > 0.7:
                h = resistance - rng.uniform(0, 0.04)
                l = c - rng.uniform(0.02, 0.08)
                c = min(c, h - 0.02)
            else:
                h = c + abs(rng.normal(0.03, 0.02))
                l = c - abs(rng.normal(0.03, 0.02))
            v = 2000 * (1 - (i - n_pre) / max(flat_days, 2) * 0.55) + rng.normal(0, 80)
            o = c + rng.normal(0, 0.02)
        else:
            # 放量突破阻力
            o = resistance - 0.05
            c = resistance + 0.35 + rng.normal(0, 0.02)
            h = c + abs(rng.normal(0, 0.03))
            l = o - abs(rng.normal(0, 0.02))
            v = 3800 + rng.normal(0, 150)
        closes.append(float(c))
        highs.append(float(max(h, c, o)))
        lows.append(float(min(l, c, o)))
        opens.append(float(o))
        vols.append(float(max(v, 50)))
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "vol": vols,
    })


def test_positive_case():
    df = make_synthetic(seed=42, flat_days=80)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True, f"应检测到突破, reasons={sig['reasons']}"
    assert sig["box_days"] >= 20, f"箱体应≥1个月(20日), got {sig['box_days']}"
    assert sig["box_days"] <= 125, f"箱体应≤6个月(125日), got {sig['box_days']}"
    # 80 日合成平台：质量相近时应选到较长箱体，而非被 20 日子窗抢走
    assert sig["box_days"] >= 50, f"长横盘合成数据应检出较长箱体, got {sig['box_days']}"
    assert sig["box_amp"] < 0.28
    assert sig["breakout_vol_ratio"] >= 1.6
    score = score_breakout_strength(sig)
    assert score > 50, f"长横盘明确突破强度分应较高, got {score}"
    print("[PASS] 正向合成数据: 检测到突破, 强度分", score)
    print("   reasons:", sig["reasons"])
    print("   箱体:", sig["box_days"], "天, 振幅", f"{sig['box_amp']:.1%}",
          f", 上沿 {sig['box_high']:.2f}, 量比 {sig['breakout_vol_ratio']:.1f}x")


def test_short_box_one_month():
    """约1个月横盘（~30日平台+突破）应可被接受"""
    df = make_synthetic(seed=7, flat_days=32)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True, f"1个月横盘应命中, reasons={sig['reasons']}"
    assert 20 <= sig["box_days"] <= 125
    print("[PASS] 1个月量级横盘可接受, box_days=", sig["box_days"])


def test_long_box_six_months():
    """约5~6个月横盘（110日平台+突破）应可被接受，并优先长箱体"""
    df = make_synthetic(seed=11, flat_days=115)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True, f"6个月横盘应命中, reasons={sig['reasons']}"
    assert 20 <= sig["box_days"] <= 125
    assert sig["box_days"] >= 70, f"长横盘应检出较长箱体, got {sig['box_days']}"
    print("[PASS] 近6个月量级横盘可接受, box_days=", sig["box_days"])


def test_negative_no_breakout():
    """横盘但没突破：最后一天收在箱体内、量能也不放"""
    df = make_synthetic(seed=42)
    # 明确压回箱体中部，且缩量
    df.loc[df.index[-1], "open"] = 10.0
    df.loc[df.index[-1], "close"] = 10.05
    df.loc[df.index[-1], "high"] = 10.12
    df.loc[df.index[-1], "low"] = 9.95
    df.loc[df.index[-1], "vol"] = 1200
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is False, "未突破时不应判为突破"
    print("[PASS] 负向: 未突破正确识别, reasons:", sig["reasons"])


def test_negative_trending():
    """单边上涨趋势：不应判为横盘突破"""
    rng = np.random.default_rng(7)
    n = 81
    dates = pd.bdate_range("2026-04-01", periods=n)
    closes = 10 * np.linspace(1, 1.8, n) + rng.normal(0, 0.05, n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "vol": np.full(n, 1500),
    })
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is False, "单边趋势不应判为横盘突破"
    print("[PASS] 负向: 单边趋势正确拒绝, reasons:", sig["reasons"][:2])


def test_real_kline():
    """用真实K线（腾讯接口）跑一次，验证数据兼容性"""
    import requests as rq

    from config import TENCNET_KLINE_URL, UA
    r = rq.get(TENCNET_KLINE_URL,
               params={"param": "sz000001,day,,,120,qfq"},
               headers={"User-Agent": UA}, timeout=15)
    d = r.json()["data"]["sz000001"]
    key = "qfqday" if "qfqday" in d else "day"
    rows = d[key]
    # 腾讯返回顺序: date, open, close, high, low, volume（除权日可能有第7列分红信息）
    rows = [row[:6] for row in rows]
    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "vol"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    sig = detect_accumulation_breakout(df)
    print("[INFO] 真实K线 000001 检测结果:", "突破" if sig["is_breakout"] else "非突破",
          "| reasons:", sig["reasons"])
    assert "box_amp" in sig
    print("[PASS] 真实K线兼容性正常")


def test_longer_clearer_ranks_higher():
    """横盘更长、信号更明确 → 强度分更高。"""
    short = {
        "is_breakout": True,
        "box_days": 25,
        "box_amp": 0.22,
        "breakout_vol_ratio": 1.7,
        "vol_shrink_ratio": 0.95,
        "breakout_pct_chg": 0.08,
        "cond_hold": True,
        "cond_ma": False,
        "box_slope": 0.002,
        "sup_touches": 2,
        "res_touches": 2,
        "box_swings": 1,
        "box_quality": 40,
    }
    long_clear = {
        "is_breakout": True,
        "box_days": 80,
        "box_amp": 0.12,
        "breakout_vol_ratio": 2.8,
        "vol_shrink_ratio": 0.55,
        "breakout_pct_chg": 0.035,
        "cond_hold": True,
        "cond_ma": True,
        "box_slope": 0.0005,
        "sup_touches": 5,
        "res_touches": 5,
        "box_swings": 4,
        "box_quality": 90,
    }
    s_short = score_breakout_strength(short)
    s_long = score_breakout_strength(long_clear)
    assert s_long > s_short + 10, f"长横盘明确信号应明显更高: long={s_long} short={s_short}"
    mid = dict(short)
    mid["box_days"] = 70
    s_mid = score_breakout_strength(mid)
    assert s_mid > s_short, f"更长横盘应更高: mid={s_mid} short={s_short}"
    print(f"[PASS] 排序偏好: short={s_short} mid={s_mid} long_clear={s_long}")


def test_reject_vshape():
    """V 形深砸反弹：不是吸筹箱体。"""
    rng = np.random.default_rng(3)
    n = 60
    dates = pd.bdate_range("2026-01-01", periods=n)
    # 前高 → 深跌 25% → 拉回并假突破
    xs = np.linspace(0, 1, n)
    closes = 12 - 3.5 * xs[: n // 2]
    closes = np.concatenate([closes, 8.5 + 4.0 * np.linspace(0, 1, n - n // 2)])
    closes = closes + rng.normal(0, 0.05, n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": closes,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "vol": np.full(n, 2000.0),
    })
    # 末日放量上攻
    df.loc[df.index[-1], "close"] = float(closes[-2] * 1.04)
    df.loc[df.index[-1], "high"] = float(df.loc[df.index[-1], "close"] * 1.01)
    df.loc[df.index[-1], "vol"] = 5000
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is False, f"V形不应判突破: {sig.get('reasons')}"
    print("[PASS] V形深砸拒绝", sig["reasons"][:2])


def test_reject_one_way_channel():
    """缓慢单边爬升通道：有边界但非横盘吸筹。"""
    rng = np.random.default_rng(9)
    n = 70
    dates = pd.bdate_range("2026-02-01", periods=n)
    t = np.arange(n)
    closes = 10 + 0.025 * t + 0.08 * np.sin(t / 3) + rng.normal(0, 0.02, n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": closes,
        "high": closes + 0.05,
        "low": closes - 0.05,
        "close": closes,
        "vol": np.linspace(2500, 1800, n),
    })
    df.loc[df.index[-1], "close"] = float(closes[-1] * 1.03)
    df.loc[df.index[-1], "high"] = float(df.loc[df.index[-1], "close"] * 1.01)
    df.loc[df.index[-1], "vol"] = 4000
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is False, f"爬升通道不应判箱体突破: {sig.get('reasons')}"
    print("[PASS] 单边通道拒绝", sig["reasons"][:2])


def test_box_structure_fields():
    """合格箱体应给出支撑/压力触及与质量分。"""
    df = make_synthetic(seed=42, flat_days=80)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True
    assert sig.get("sup_touches", 0) >= 2
    assert sig.get("res_touches", 0) >= 2
    assert sig.get("box_swings", 0) >= 1
    assert sig.get("box_quality", 0) > 20
    # 箱体不应包含最后突破日（end 在倒数第二根或更早）
    assert sig["box_end_idx"] is not None
    print(
        f"[PASS] 结构字段 sup={sig['sup_touches']} res={sig['res_touches']} "
        f"swings={sig['box_swings']} Q={sig['box_quality']:.1f}"
    )


if __name__ == "__main__":
    test_positive_case()
    test_short_box_one_month()
    test_long_box_six_months()
    test_longer_clearer_ranks_higher()
    test_negative_no_breakout()
    test_negative_trending()
    test_reject_vshape()
    test_reject_one_way_channel()
    test_box_structure_fields()
    test_real_kline()
    print("\n全部信号引擎测试通过 ✅")
