"""组合回测引擎：DSL 信号 → 逐笔交易 → 绩效（复用宿主 trade_sim）。

- 逐笔出场复用宿主 `trade_sim.simulate_trade(mode="fixed")`，
  DSL exit 参数（stop_pct/target_pct/max_hold）直接映射
- 入场：信号日次日开盘（trade_sim 约定），无 open 用 close
- 持有期不足（信号日临近数据末端）→ 记 truncated，不参与指标
- 组合级指标：total_return（逐笔累乘）+ 组合最大回撤（equity 曲线）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd

from logic_platform.dsl.schema import ExitParams

_LOGGER = logging.getLogger(__name__)

# 信号日之后补拉持有期余量（交易日）
_HOLD_PAD_DAYS = 40


@dataclass
class BacktestResult:
    run_id: str
    strategy_id: str
    signals_count: int
    trades: list[dict]
    metrics: dict
    truncated: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def to_json(self) -> dict:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "signals_count": self.signals_count,
            "truncated": self.truncated,
            "metrics": self.metrics,
            "trades": self.trades,
        }


def _equity_drawdown(rets: list[float]) -> float:
    """逐笔累乘 equity 曲线的最大回撤。"""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
    return round(float(mdd), 4)


def run_backtest(
    signals: list[dict],
    store,
    exit_params: ExitParams,
    strategy_id: str,
    end: str,
    early: str | None = None,
    lookback_bars: int = 180,
) -> BacktestResult:
    """执行回测。

    signals: interpreter.run 输出的信号 dict 列表（含 ts_code/signal_date）
    store:  ABStore（数据源）
    """
    if not signals:
        return BacktestResult(
            run_id=uuid.uuid4().hex[:12], strategy_id=strategy_id,
            signals_count=0, trades=[], metrics=_empty_metrics(),
            errors=[],
        )

    # 按股票分组，每只加载一次（early→end）
    by_code: dict[str, list[dict]] = {}
    for s in signals:
        by_code.setdefault(s["ts_code"], []).append(s)

    pad_end = end  # 宿主库已含 end 之后数据（latest>end），直接加载即可
    trades: list[dict] = []
    truncated = 0
    errors: list[str] = []

    params = {
        "stop_pct": exit_params.stop_pct,
        "target_pct": exit_params.target_pct,
        "max_hold": exit_params.max_hold,
    }

    for code, sigs in by_code.items():
        try:
            df = store.ohlcv(code, start=early, end=pad_end)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{code}: 加载失败 {exc}")
            continue
        if df is None or df.empty or "date" not in df.columns:
            errors.append(f"{code}: 无数据")
            continue
        date_idx = {d: i for i, d in enumerate(df["date"].astype(str).tolist())}

        for s in sigs:
            entry_i = date_idx.get(s["signal_date"])
            if entry_i is None:
                errors.append(f"{code}: 信号日 {s['signal_date']} 不在数据内")
                continue
            if entry_i + 1 >= len(df):
                truncated += 1
                continue
            # 剩余 bars 不足以完成 max_hold → 截断
            remaining = len(df) - entry_i - 1
            if remaining < exit_params.max_hold:
                truncated += 1
                continue
            t = simulate_trade(df, entry_i, params)
            if not t.get("ok"):
                errors.append(f"{code}: 交易模拟失败 @{s['signal_date']}")
                continue
            t["ts_code"] = code
            t["signal_date"] = s["signal_date"]
            t["state"] = s.get("state")
            t["reasons"] = s.get("reasons", [])
            trades.append(t)

    metrics = summarize_trades(trades)
    return BacktestResult(
        run_id=uuid.uuid4().hex[:12], strategy_id=strategy_id,
        signals_count=len(signals), trades=trades, metrics=metrics,
        truncated=truncated, errors=errors,
    )


def simulate_trade(df: pd.DataFrame, entry_i: int, params: dict) -> dict:
    """薄封装宿主 trade_sim.simulate_trade（fixed 模式）。"""
    from trade_sim import simulate_trade as _sim

    return _sim(df, entry_i, mode="fixed", params=params)


def summarize_trades(trades: list[dict]) -> dict:
    """交易级 + 组合级绩效。"""
    if not trades:
        return _empty_metrics()
    rets = [float(t["ret"]) for t in trades]
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]

    metrics: dict = {
        "n_trades": len(trades),
        "win_rate": round(float(np.mean([t["win"] for t in trades])), 4),
        "avg_ret": round(float(np.mean(rets)), 4),
        "median_ret": round(float(np.median(rets)), 4),
        "total_return": round(float(np.prod([1 + r for r in rets]) - 1), 4),
        "max_drawdown": _equity_drawdown(rets),
        "avg_hold_days": round(float(np.mean([t.get("days", 0) for t in trades])), 1),
        "profit_factor": None,
        "exits": _exit_counts(trades),
        "avg_win": round(float(np.mean([t["ret"] for t in wins])), 4) if wins else None,
        "avg_loss": round(float(np.mean([t["ret"] for t in losses])), 4) if losses else None,
    }
    if wins and losses:
        g = sum(t["ret"] for t in wins)
        l_abs = abs(sum(t["ret"] for t in losses))
        if l_abs > 1e-9:
            metrics["profit_factor"] = round(float(g / l_abs), 3)
    return metrics


def _exit_counts(trades: list[dict]) -> dict:
    out: dict[str, int] = {}
    for t in trades:
        out[t["exit"]] = out.get(t["exit"], 0) + 1
    return out


def _empty_metrics() -> dict:
    return {
        "n_trades": 0, "win_rate": None, "avg_ret": None, "median_ret": None,
        "total_return": None, "max_drawdown": None, "avg_hold_days": None,
        "profit_factor": None, "exits": {}, "avg_win": None, "avg_loss": None,
    }
