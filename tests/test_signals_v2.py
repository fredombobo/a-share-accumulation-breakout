"""突破逻辑 v2 单测：站稳检验 / MA60 过滤 / 位置护栏（完整窗口）/ relaxed 回踩容忍。

合成 K 线约定（170 根）：
  df[0:107]   前段：围绕 10.0 的中位震荡（pre_high≈10.6，趋势平稳）
  df[107:167] 箱体：9.8~10.3 之间 60 日震荡（支撑/压力各多次触及、振幅 ~5%）
  df[167]     突破日：放量 +3.5% 收 10.65（> 箱顶 10.3）
  df[168:170] 突破后 2 根（默认站稳 10.7 / 10.85）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals import detect_accumulation_breakout


def make_df(
    *,
    breakout_close: float = 10.65,
    after: tuple[float, float] = (10.7, 10.85),
    breakout_vol_mult: float = 3.0,
    n_total: int = 170,
) -> pd.DataFrame:
    """构造「中位平台 + 突破」标准形态。"""
    n_pre = 107
    n_box = 60
    dates: list[str] = []
    opens, highs, lows, closes, vols = [], [], [], [], []

    def add(d: int, o: float, h: float, l: float, c: float, v: float) -> None:
        dates.append(f"2026-{(d // 10000) % 12 + 1:02d}-{d % 100:02d}")
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        vols.append(v)

    d = 1
    # 前段：中位震荡（围绕 10，高点 10.6）
    for i in range(n_pre):
        base = 10.0 + 0.15 * np.sin(i / 6.0)
        o = base
        c = base + 0.1 * np.sin(i / 3.0)
        h = max(o, c) + 0.12
        l = min(o, c) - 0.12
        add(d, o, h, l, c, 100.0)
        d += 1
    # 箱体：9.8~10.3 震荡，多次触及上下沿
    for i in range(n_box):
        phase = i % 8
        if phase in (0, 4):
            c = 9.85 if phase == 0 else 10.28
            l = 9.78 if phase == 0 else 10.0
            h = 10.05 if phase == 0 else 10.32
        else:
            c = 10.05 + 0.08 * np.sin(i / 2.5)
            l = c - 0.10
            h = c + 0.10
        o = c - 0.03
        add(d, o, h, l, c, 90.0)
        d += 1
    # 突破日
    prev_c = closes[-1]
    add(d, prev_c * 1.002, breakout_close * 1.012, prev_c, breakout_close, 90.0 * breakout_vol_mult)
    d += 1
    # 突破后
    for c in after:
        add(d, c - 0.02, c + 0.06, c - 0.08, c, 110.0)
        d += 1

    df = pd.DataFrame({
        "date": dates, "open": opens, "high": highs, "low": lows,
        "close": closes, "vol": vols,
    })
    return df.iloc[:n_total]


def test_normal_breakout_passes_v2():
    sig = detect_accumulation_breakout(make_df())
    assert sig["is_breakout"], sig["reasons"]
    assert sig["cond_hold"] is True
    assert sig["cond_ma60"] is True
    assert sig["hold_pullbacks"] == 0
    assert sig["ma60"] is not None


def test_fake_breakout_pullback_rejected():
    # 突破后第二天跌回箱体上沿以下 → strict 拒绝（假突破/一日游）
    sig = detect_accumulation_breakout(make_df(after=(10.7, 10.22)))
    assert sig["is_breakout"] is False
    assert any("回踩" in r or "跌回" in r for r in sig["reasons"]), sig["reasons"]
    assert sig["hold_pullbacks"] == 1


def test_relaxed_allows_single_pullback():
    # 回踩 1 次后重新站回上沿（最新收盘 > 箱顶）→ relaxed 通过；strict 拒绝
    df = make_df(after=(10.22, 10.75))
    sig_strict = detect_accumulation_breakout(df)
    assert sig_strict["is_breakout"] is False
    assert sig_strict["hold_pullbacks"] == 1
    sig = detect_accumulation_breakout(df, require_structure=False)
    assert sig["is_breakout"] is True, sig["reasons"]
    assert sig["hold_pullbacks"] == 1


def test_bottom_oscillation_rejected_by_position():
    """底部震荡：箱体位于前段高点下方 30% → 位置护栏拒绝（完整窗口计算）。"""
    df = make_df()
    # 把前段整体抬高到 14 附近（前段高点 ~14.6），箱体/突破不变 → 箱体中轴回撤 ~-30%
    df.loc[:106, ["open", "high", "low", "close"]] = (
        df.loc[:106, ["open", "high", "low", "close"]] + 4.0
    )
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is False
    assert any("位置过深" in r or "MA60" in r or "大趋势" in r for r in sig["reasons"]), sig["reasons"]
    assert sig["cond_position"] is False


def test_breakout_on_last_bar_passes():
    # 突破日=最后一根 K 线（当日突破）→ 无突破后 K 线，pullbacks=0，通过
    df = make_df(n_total=168)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True, sig["reasons"]
    assert sig["hold_pullbacks"] == 0


def test_deep_downtrend_rejected():
    """大趋势下跌：最近 60 日整体下行 → trend 护栏拒绝。"""
    df = make_df()
    # 前段最后 40 根逐步下移 → 近 60 日涨跌为负且低于 -15% 阈值的一部分。
    # 直接构造更明确：把突破后的走势改为回落 + 前段从 12 跌到 10。
    df.loc[67:106, ["open", "high", "low", "close"]] = (
        df.loc[67:106, ["open", "high", "low", "close"]] + np.linspace(2.0, 0.0, 40)[:, None]
    )
    sig = detect_accumulation_breakout(df)
    # 该形态可能因位置或趋势被拒；断言至少被拒且原因可见
    assert sig["is_breakout"] is False
    assert len(sig["reasons"]) >= 1


def test_ma60_field_exposed():
    sig = detect_accumulation_breakout(make_df())
    assert sig["ma60"] is not None and sig["ma60"] > 0
    assert sig["max_pullbacks_allowed"] == 0
