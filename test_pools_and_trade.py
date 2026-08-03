"""P0/P1 验收：池拆分、交易卡片、环境、新鲜度"""
from __future__ import annotations

import os
import sys

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from market_regime import data_freshness, detect_regime_from_index_df
from pool_select import split_pools, breakout_freshness_bonus, apply_soft_theme_bonus
from trade_plan import build_trade_card
from portfolio import upsert_position, check_stops, load_portfolio, remove_position, PORTFOLIO_PATH
from pathlib import Path
import tempfile


def test_split_pools_keeps_theme_fill_out_of_a():
    rows = []
    for i in range(10):
        rows.append({
            "ts_code": f"A{i:04d}.SZ", "名称": f"严格{i}", "行业": "软件服务",
            "综合分": 90 - i, "筛选层级": "strict",
        })
    for i in range(10):
        rows.append({
            "ts_code": f"B{i:04d}.SZ", "名称": f"主题{i}", "行业": "半导体",
            "综合分": 95 - i, "筛选层级": "theme_fill",  # 更高分也不得进 A
        })
    df = pd.DataFrame(rows)
    a, b, rep = split_pools(df, top_a=15, top_b=20)
    assert len(a) == 10
    assert (a["筛选层级"] == "strict").all()
    assert len(b) == 10
    assert (b["筛选层级"] == "theme_fill").all()
    print("[PASS] theme_fill 不进 A 池", rep)


def test_trade_card_and_freshness():
    card = build_trade_card(price=10.0, box_high=9.5, box_low=8.0, breakout_date="20260730", tier="strict", regime="attack")
    assert card["stop_loss"] is not None and card["stop_loss"] < 10
    assert card["target_1"] is not None and card["target_1"] > 10
    assert card["tradeable"] is True
    card_b = build_trade_card(price=10.0, box_high=9.5, box_low=8.0, breakout_date="20260730", tier="theme_fill", regime="attack")
    assert card_b["tradeable"] is False
    assert breakout_freshness_bonus("20260731", "20260731") >= 5
    fr = data_freshness("20260701", today="20260803")
    assert fr["is_stale"] is True
    print("[PASS] 交易卡片与新鲜度")


def test_regime_defense():
    dates = pd.bdate_range("2026-01-01", periods=40)
    # 单边下跌
    closes = [100 - i * 0.8 for i in range(40)]
    df = pd.DataFrame({"trade_date": dates.strftime("%Y%m%d"), "close": closes})
    r = detect_regime_from_index_df(df)
    assert r.regime in ("defense", "neutral")
    print("[PASS] 环境识别", r.label, r.allow_new_entries)


def test_portfolio_roundtrip():
    tmp = Path(tempfile.mkdtemp()) / "portfolio.json"
    upsert_position("000001.SZ", name="平安银行", cost=10, stop_loss=9.3, path=tmp)
    alerts = check_stops({"000001.SZ": 9.0}, path=tmp)
    assert any(a["status"] == "STOP_HIT" for a in alerts)
    remove_position("000001.SZ", path=tmp)
    assert load_portfolio(tmp)["positions"] == []
    print("[PASS] 持仓止损检查")


if __name__ == "__main__":
    test_split_pools_keeps_theme_fill_out_of_a()
    test_trade_card_and_freshness()
    test_regime_defense()
    test_portfolio_roundtrip()
    print("\n全部 P0/P1 单元测试通过 ✅")
