"""通用回测引擎（工作台专用）：单组参数 → 逐笔交易明细 + 净成本指标 + 权益曲线。

与可信研究管线（trusted_run）共享信号/出场/成本实现，但输出粒度到逐笔，
供「回测工作台」渲染权益曲线、交易明细表与指标仪表盘。

多进程安全：成本覆盖在 worker 子进程内临时应用到 ab_screener.domain.costs
模块属性（每进程独立命名空间），父进程不受影响。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from ab_screener.domain.entry_registry import active_definition_id as _active_entry_definition_id
from ab_screener.domain.entry_registry import report_entry_fingerprint
from config import BENCH_MAX_HOLD_DAYS, BT_MIN_TRADES

if TYPE_CHECKING:
    from ab_screener.research.pit_reader import ResearchPitSnapshot
    from ab_screener.research.portfolio_accounting import PortfolioPolicy


def apply_cost_override(costs: dict | None) -> None:
    """在**当前进程**内临时覆盖成本常量（仅影响本进程后续计算）。"""
    if not costs:
        return
    import ab_screener.domain.costs as C

    if costs.get("commission_rate") is not None:
        C.COMMISSION_RATE = float(costs["commission_rate"])
    if costs.get("commission_min") is not None:
        C.COMMISSION_MIN = float(costs["commission_min"])
    if costs.get("stamp_tax_sell") is not None:
        C.STAMP_TAX_SELL = float(costs["stamp_tax_sell"])
    if costs.get("other_fee_rate") is not None:
        C.OTHER_FEE_RATE = float(costs["other_fee_rate"])
    if costs.get("slippage") is not None:
        C.SLIPPAGE = float(costs["slippage"])


def run_single_backtest(
    *,
    strategy: str = "A",
    exit_params: dict[str, Any] | None = None,
    signal_kwargs: dict[str, Any] | None = None,
    costs: dict | None = None,
    start: str,
    end: str,
    step: int = 10,
    max_codes: int | None = None,
    workers: int | None = None,
    cancel_check=None,
    progress_cb=None,
    portfolio_policy: PortfolioPolicy | None = None,
    research_snapshot: ResearchPitSnapshot | None = None,
    allowed_signal_dates: frozenset[str] | set[str] | None = None,
    horizon: int | None = None,
) -> dict[str, Any]:
    """对单组参数在 [start, end] 区间回放，返回逐笔明细与指标。

    返回:
      {universe_n, sample_days, window: [start, end], params,
       trades: [{ts_code, signal_date, entry_date, exit_date, entry_price,
                 exit_price, exit, ret, net_return, days, max_dd, commission,
                 stamp_tax, other_fee, slippage_cost, filled}],
       metrics: {n_trades, win_rate, avg_ret, profit_factor, net_*},
       equity: [{date, eq, drawdown}]}
    """
    from ab_screener.research.cost_adjustment import summarize_costed_trades
    from local_store import LocalStore
    from optimizer import research_universe
    from trade_sim import summarize

    signal_options = signal_kwargs or {}
    resolved_horizon = int(
        horizon
        or max(
            260,
            int(signal_options.get("box_max_days") or 0)
            + int(signal_options.get("breakout_window_days") or 0)
            + 60,
        )
    )
    if resolved_horizon < 60:
        raise ValueError("horizon 至少为 60 个交易日")
    if progress_cb:
        progress_cb("加载宇宙与行情…", 2)
    load_start = (
        pd.to_datetime(start) - pd.Timedelta(days=max(365, resolved_horizon * 2))
    ).strftime("%Y%m%d")
    if research_snapshot is not None:
        codes = list(research_snapshot.universe)
        if max_codes is not None:
            codes = codes[:max_codes]
        big = research_snapshot.load_daily(ts_codes=codes, start=load_start, end=end)
        cal = sorted(big["trade_date"].astype(str).unique().tolist())
    else:
        store = LocalStore()
        codes = research_universe(max_codes, include_delisted=True)
        cal = store.distinct_dates("daily")
        big = store.load_daily(ts_codes=codes, start=load_start, end=end)
    from ab_screener.research.resilient_absorption import prepare_signal_market_context

    big = prepare_signal_market_context(
        big,
        research_snapshot=research_snapshot,
        start=load_start,
        end=end,
        signal_kwargs=signal_kwargs,
    )
    sample_days = [d for d in cal if start <= d <= end][:: max(1, step)]
    if not sample_days:
        return {"error": "窗口内无采样日"}

    if big.empty:
        return {"error": "无行情数据，请先同步"}

    combo = {
        "strategy": strategy,
        "vol_ratio_min": float((exit_params or {}).get("vol_ratio_min") or 1.5),
        "strong_reset": int((exit_params or {}).get("strong_reset") or 3),
        "exit_window": int((exit_params or {}).get("exit_window") or 10),
        "max_hold_days": int(
            (exit_params or {}).get("max_hold_days") or BENCH_MAX_HOLD_DAYS
        ),
        "stop_pct": float((exit_params or {}).get("stop_pct") or 0.07),
    }
    if (exit_params or {}).get("target_pct") is not None:
        combo["target_pct"] = float((exit_params or {})["target_pct"])
    vr_levels = [combo["vol_ratio_min"]]
    combos = [combo]

    from parallel_scan import resolve_workers

    nw = resolve_workers(workers)
    results: dict[str, list[dict]] = {}
    if len(codes) < 100 or nw <= 1:
        from optimizer import _worker_chunk

        results = _worker_chunk(
            (
                codes,
                big,
                sample_days,
                cal,
                resolved_horizon,
                strategy,
                vr_levels,
                combos,
                signal_kwargs,
                costs,
                allowed_signal_dates,
            )
        )
        if progress_cb:
            progress_cb("单进程回放完成", 90)
    else:
        from concurrent.futures import ProcessPoolExecutor, wait

        from optimizer import _worker_chunk

        chunk_size = max(50, (len(codes) + nw - 1) // nw)
        chunks = [codes[i : i + chunk_size] for i in range(0, len(codes), chunk_size)]
        pool = ProcessPoolExecutor(max_workers=nw)
        try:
            futs = [
                pool.submit(
                    _worker_chunk,
                    (
                        ch,
                        big[big["ts_code"].isin(ch)].copy(),
                        sample_days,
                        cal,
                        resolved_horizon,
                        strategy,
                        vr_levels,
                        combos,
                        signal_kwargs,
                        costs,
                        allowed_signal_dates,
                    ),
                )
                for ch in chunks
            ]
            done = 0
            pending = set(futs)
            while pending:
                if cancel_check is not None and cancel_check():
                    from scan_runtime import abandon_pool

                    abandon_pool(pool)
                    return {"error": "已取消"}
                finished, pending = wait(pending, timeout=0.5, return_when="FIRST_COMPLETED")
                for fut in finished:
                    try:
                        for pid, chunk_trades in fut.result().items():
                            results.setdefault(pid, []).extend(chunk_trades)
                    except Exception as exc:  # noqa: BLE001
                        if progress_cb:
                            progress_cb(f"警告：分片异常 {exc}", done)
                    done += 1
                    if progress_cb:
                        progress_cb(f"回放分片 {done}/{len(chunks)}", 5 + int(85 * done / len(chunks)))
        finally:
            pool.shutdown(wait=True)

    trades_raw = results.get("", []) or next(iter(results.values()), [])
    # 逐笔明细（含成本拆解）
    trades: list[dict[str, Any]] = []

    for t in trades_raw:
        cost = t.get("cost") or {}
        trades.append(
            {
                "ts_code": t.get("ts_code") or "",
                "signal_date": t.get("date") or "",
                "entry_date": t.get("entry_date") or "",
                "exit_date": t.get("exit_date") or "",
                "box_high": t.get("box_high"),
                "box_low": t.get("box_low"),
                "breakout_date": t.get("breakout_date") or "",
                "entry_price": t.get("entry"),
                "exit_price": cost.get("price") if cost.get("filled") else None,
                "exit": t.get("exit") or "",
                "ret": round(float(t.get("ret") or 0.0), 6),
                "net_return": cost.get("net_return"),
                "filled": bool(cost.get("filled")),
                "days": t.get("days"),
                "max_dd": t.get("max_dd"),
                "commission": cost.get("commission"),
                "stamp_tax": cost.get("stamp_tax"),
                "other_fee": cost.get("other_fee"),
                "slippage_cost": cost.get("slippage_cost"),
                "reason": cost.get("reason") or "",
                "entry_mechanism": t.get("entry_mechanism"),
            }
        )

    gross = summarize(trades_raw)
    trade_net = summarize_costed_trades(trades_raw)

    portfolio: dict[str, Any] | None = None
    if portfolio_policy is not None:
        from ab_screener.research.portfolio_accounting import (
            portfolio_gate_metrics,
            simulate_portfolio,
        )

        portfolio = simulate_portfolio(trades_raw, big, policy=portfolio_policy)
        net = portfolio_gate_metrics(portfolio)
        equity = [
            {
                "date": row["trade_date"],
                "eq": round(
                    int(row["equity_fen"]) / portfolio_policy.initial_cash_fen,
                    8,
                ),
                "drawdown": row["drawdown"],
                "cash_fen": row["cash_fen"],
                "market_value_fen": row["market_value_fen"],
                "position_count": row["position_count"],
            }
            for row in portfolio["equity_curve"]
        ]
    else:
        net = trade_net
        # 兼容旧工作台：只有未指定组合模型时才保留逐笔顺序复利曲线。
        equity = []
        filled = [t for t in trades_raw if t.get("cost", {}).get("filled")]
        ordered = sorted(
            filled,
            key=lambda r: (str(r.get("date") or ""), str(r.get("ts_code") or "")),
        )
        eq = 1.0
        peak = 1.0
        for row in ordered:
            eq *= 1.0 + float(row["cost"]["net_return"])
            peak = max(peak, eq)
            equity.append(
                {
                    "date": row.get("date") or "",
                    "ts_code": row.get("ts_code") or "",
                    "eq": round(eq, 6),
                    "drawdown": round(1.0 - eq / peak, 6) if peak > 0 else 0.0,
                }
            )

    if progress_cb:
        progress_cb("统计与权益曲线完成", 100)

    return {
        "universe_n": len(codes),
        "sample_days": sample_days,
        "window": [start, end],
        "params": {
            "strategy": strategy,
            "exit": combo,
            "signal": signal_kwargs or None,
            "costs": costs or None,
            "step": step,
            "horizon": resolved_horizon,
        },
        "entry_definition": report_entry_fingerprint(_active_entry_definition_id()),
        "trades": trades,
        "metrics": {
            **gross,
            **({f"trade_{key}": value for key, value in trade_net.items()} if portfolio else {}),
            **net,
        },
        "equity": equity,
        "portfolio": portfolio,
        "min_trades": BT_MIN_TRADES,
    }
