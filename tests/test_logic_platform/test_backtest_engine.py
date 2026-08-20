"""回测引擎测试：模拟信号 → 交易 → 绩效。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from logic_platform.backtest.engine import run_backtest, summarize_trades
from logic_platform.dsl.schema import ExitParams


class _FakeStore:
    """注入固定 K 线的最小 ABStore 替身（只暴露 ohlcv）。"""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def ohlcv(self, ts_code, start=None, end=None, limit=None):
        return self._df


def _make_df(n=160, trend=0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2026-01-05", periods=n)
    close = 10 + np.arange(n) * trend + rng.normal(0, 0.1, n)
    close = np.maximum(close, 1.0)
    df = pd.DataFrame({
        "ts_code": "TEST.SZ",
        "trade_date": dates.strftime("%Y%m%d"),
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.995,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "pre_close": np.roll(close, 1),
        "change": np.zeros(n), "pct_chg": np.zeros(n),
        "vol": np.full(n, 2e6), "amount": np.full(n, 1e8),
    })
    df.loc[0, "pre_close"] = df.loc[0, "close"]
    return df


def _signals(dates: list[str]) -> list[dict]:
    return [{"ts_code": "TEST.SZ", "as_of": d, "signal_date": d,
             "state": "BREAKOUT", "reasons": ["r1"], "features": {}, "box": None}
            for d in dates]


def test_summarize_trades_basic():
    trades = [
        {"ret": 0.05, "win": True, "days": 3, "exit": "target", "max_dd": 0.02},
        {"ret": -0.07, "win": False, "days": 5, "exit": "stop", "max_dd": 0.07},
        {"ret": 0.02, "win": True, "days": 4, "exit": "time", "max_dd": 0.03},
    ]
    m = summarize_trades(trades)
    assert m["n_trades"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert m["profit_factor"] == pytest.approx(0.07 / 0.07, abs=0.01)
    assert m["exits"] == {"target": 1, "stop": 1, "time": 1}


def test_summarize_empty():
    m = summarize_trades([])
    assert m["n_trades"] == 0
    assert m["win_rate"] is None


def test_run_backtest_executes_trades():
    df = _make_df(160, trend=0.01)
    store = _FakeStore(df)
    sigs = _signals([str(df["date"].iloc[i]) for i in (20, 50, 80, 110)])
    bt = run_backtest(sigs, store, ExitParams(), "t1", end="2026-12-31",
                      early="2026-01-01")
    assert bt.signals_count == 4
    assert bt.metrics["n_trades"] == 4
    assert "total_return" in bt.metrics
    assert "max_drawdown" in bt.metrics
    # 趋势向上 → 整体正收益
    assert bt.metrics["total_return"] > 0


def test_run_backtest_truncated_near_end():
    df = _make_df(60, trend=0.0)
    store = _FakeStore(df)
    # 信号日在末尾 → 剩余 bars < max_hold → 截断
    sigs = _signals([str(df["date"].iloc[55])])
    bt = run_backtest(sigs, store, ExitParams(max_hold=15), "t1", end="2026-12-31")
    assert bt.truncated == 1
    assert bt.metrics["n_trades"] == 0


def test_run_backtest_unknown_signal_date():
    df = _make_df(80)
    store = _FakeStore(df)
    bt = run_backtest(_signals(["1999-01-01"]), store, ExitParams(), "t1",
                      end="2026-12-31")
    assert bt.metrics["n_trades"] == 0
    assert any("1999-01-01" in e for e in (bt.errors or []))


def test_run_backtest_empty_signals():
    store = _FakeStore(_make_df(80))
    bt = run_backtest([], store, ExitParams(), "t1", end="2026-12-31")
    assert bt.metrics["n_trades"] == 0
    assert bt.signals_count == 0
