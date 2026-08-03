"""Phase4 + residual acceptance tests"""
from __future__ import annotations
import os, sys
os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from market_regime import detect_regime_from_index_df, data_freshness
from pool_select import fund_flow_quality_ok, breakout_freshness_bonus, split_pools
from prefilter_fast import volume_breakout_candidates

def test_regime_defense_strict():
    dates = pd.bdate_range("2026-01-01", periods=40)
    closes = [100 - i * 1.0 for i in range(40)]  # strong down
    df = pd.DataFrame({"trade_date": dates.strftime("%Y%m%d"), "close": closes})
    r = detect_regime_from_index_df(df)
    assert r.regime == "defense", r
    assert r.allow_new_entries is False
    assert r.max_trade_slots == 0
    print("[PASS] defense strict", r.label)

def test_fund_quality_no_column_fails():
    df = pd.DataFrame({"x": [1, 2, 3]})
    ok, n = fund_flow_quality_ok(df, 2)
    assert ok is False and n == 0
    df2 = pd.DataFrame({"net_mf_amount": [10, -1, 5, 3]})
    ok2, n2 = fund_flow_quality_ok(df2, 2)
    assert ok2 is True and n2 >= 2
    print("[PASS] fund quality gate")

def test_freshness_trading_days():
    td = ["20260728","20260729","20260730","20260731"]
    # lag 1 trading day
    b = breakout_freshness_bonus("20260730", "20260731", trade_dates=td)
    assert b == 5.0, b
    # 周五数据 + 周一（中间周末不是交易日）→ 滞后 0 交易日
    # 仅含真实开市日：7/31 五、8/3 一
    td2 = ["20260728", "20260729", "20260730", "20260731", "20260803"]
    from datetime import datetime
    fr = data_freshness(
        "20260731",
        today="20260803",
        trade_dates=td2,
        now=datetime(2026, 8, 3, 10, 0, 0),  # 周一上午，期望数据仍为上一交易日 7/31
    )
    assert fr["unit"] == "trading", fr
    assert fr["stale_days"] == 0, fr  # 排除周末后不应显示滞后 3 天
    assert fr["label"] == "新鲜", fr
    print("[PASS] trading-day freshness excludes weekend", b, fr)

def test_prefilter_and_split():
    # synthetic daily
    rows = []
    for code in ["AAA.SZ", "BBB.SZ"]:
        for i, d in enumerate(pd.bdate_range("2026-06-01", periods=30)):
            vol = 1000 if code == "BBB.SZ" else (1000 if i < 28 else 5000)
            rows.append({"ts_code": code, "trade_date": d.strftime("%Y%m%d"), "close": 10+i*0.01, "high": 10.2+i*0.01, "vol": vol})
    daily = pd.DataFrame(rows)
    keep = volume_breakout_candidates(daily, ["AAA.SZ", "BBB.SZ"], vol_ratio_min=1.5)
    assert "BBB.SZ" in keep
    print("[PASS] prefilter keep", keep)

if __name__ == "__main__":
    test_regime_defense_strict()
    test_fund_quality_no_column_fails()
    test_freshness_trading_days()
    test_prefilter_and_split()
    print("\nPhase4 tests OK")
