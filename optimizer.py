"""网格参数优化器（P4）

核心架构：信号缓存 + 参数重放解耦
- 入场检测与出场参数解耦：每只股票每个采样日只 detect 一次（B 方案按 vol_ratio_min 分档），
  缓存信号；108 组出场参数只重放轻量 simulate_trade，避免 27 万×108 次重复检测。
- 并行：复用 parallel_scan 的 spawn 进程池模式（worker 载荷可 pickle、父进程看门狗）。

输出：每组参数一行统计（DataFrame），并写入 param_eval（P5 接入）。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, wait
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from ab_screener.research.cost_adjustment import cost_adjusted_trade, summarize_costed_trades
from ab_screener.research.portfolio_accounting import (
    PortfolioAccountingError,
    PortfolioPolicy,
    portfolio_gate_metrics,
    prepare_portfolio_market,
    simulate_portfolio,
)
from config import BT_MIN_TRADES, GRID_BENCH
from trade_sim import simulate_trade, summarize

if TYPE_CHECKING:
    from ab_screener.research.pit_reader import ResearchPitSnapshot

_MIN_CODES_FOR_POOL = 100


class ResearchCancelled(RuntimeError):
    """Raised when a persisted Lab cancellation stops research work."""


def _is_cancelled(cancel_check: Any) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:  # noqa: BLE001
        return False


def _collect_pool_results(
    pool: ProcessPoolExecutor,
    pending: set,
    *,
    chunk_count: int,
    progress_cb: Any,
    cancel_check: Any,
    chunk_codes: dict | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Collect optimizer chunks while allowing an immediate hard cancel.

    Returns (results, lost_codes)；lost_codes 记录因分片异常而完全未覆盖的股票。
    """
    results: dict[str, list[dict]] = {}
    lost_codes: list[str] = []
    done = 0
    while pending:
        if _is_cancelled(cancel_check):
            from scan_runtime import abandon_pool

            abandon_pool(pool)
            raise ResearchCancelled("用户取消")
        finished, pending = wait(pending, timeout=0.5, return_when="FIRST_COMPLETED")
        for fut in finished:
            try:
                for pid, trades in fut.result().items():
                    results.setdefault(pid, []).extend(trades)
            except Exception as exc:  # noqa: BLE001
                codes = (chunk_codes or {}).get(fut, [])
                lost_codes.extend(codes)
                print(f"[optimizer][warn] 分片失败: {exc}（该分片 {len(codes)} 只股票结果缺失）")
            done += 1
            if progress_cb:
                progress_cb(f"分片 {done}/{chunk_count}", 5 + int(90 * done / chunk_count))
    if lost_codes:
        print(
            f"[optimizer][warn] 对账：{len(set(lost_codes))} 只股票的参数重放结果缺失"
            "（分片异常），相关统计将被低估",
        )
    return results, list(set(lost_codes))


_UNIVERSE_DAILY_CACHE: tuple[list[str], str] | None = None  # (codes, max_trade_date 指纹)


def research_universe(
    max_codes: int | None = None,
    include_delisted: bool = False,
    as_of: str | None = None,
) -> list[str]:
    """Return the deterministic stock universe shared by candidate and baselines.

    include_delisted=True（回测/研究路径）：宇宙 = 当前上市 ∪ 已退市（在 daily 有行情的），
    消除幸存者偏差——历史回测必须包含退市股。选股扫描路径保持 False（只选当前可交易标的）。
    指数代码（如 000300.SH）不在 stock_basic/delisted_basic 中，天然被排除。

    as_of（v2 P1.2 路径）：走 instrument 注册表按时点过滤（[list_date, delist_date)），
    注册表为空/未迁移 → 抛错（fail-closed，不使用全市场默认兜底）。
    """
    if as_of is not None:
        return _research_universe_asof(max_codes=max_codes, as_of=as_of)

    from local_store import LocalStore

    store = LocalStore()
    basic = store.load_stock_basic()
    if basic.empty:
        return []
    codes = sorted(set(basic["ts_code"].astype(str).tolist()))
    codes = [c for c in codes if c.endswith((".SH", ".SZ")) and not c.startswith(("4", "8", "92"))]

    if include_delisted:
        global _UNIVERSE_DAILY_CACHE
        max_date = store.max_trade_date("daily") or ""
        if _UNIVERSE_DAILY_CACHE is None or _UNIVERSE_DAILY_CACHE[1] != max_date:
            with store._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT ts_code FROM daily WHERE ts_code LIKE '%.SZ' OR ts_code LIKE '%.SH'"
                ).fetchall()
            daily_codes = sorted({str(r[0]) for r in rows})
            _UNIVERSE_DAILY_CACHE = (daily_codes, max_date)
        daily_codes = _UNIVERSE_DAILY_CACHE[0]
        codes = sorted(
            set(codes)
            | {c for c in daily_codes if c.endswith((".SH", ".SZ")) and not c.startswith(("4", "8", "92"))}
        )

    return codes[:max_codes] if max_codes else codes


def _research_universe_asof(max_codes: int | None, as_of: str) -> list[str]:
    """P1.2 as-of 宇宙：instrument 注册表按时点过滤（fail-closed）。"""
    from ab_screener.data.instrument_repository import (
        InstrumentRegistryError,
        universe_asof,
    )
    from local_store import LocalStore

    store = LocalStore()
    with store._connect() as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instrument_universe_rules'"
        ).fetchone()
        if not has_table:
            raise InstrumentRegistryError(
                "instrument_universe_rules 表不存在：先执行 migrate_v2.py --apply（fail-closed）"
            )
        codes = universe_asof(conn, as_of, security_types=("stock",))
    if not codes:
        raise InstrumentRegistryError(
            f"as_of={as_of} 的 instrument 宇宙为空（注册表未回填或该时点无有效规则）"
        )
    return codes[:max_codes] if max_codes else codes


def param_id(strategy: str, params: dict) -> str:
    """参数组合的稳定 hash 主键。"""
    blob = json.dumps({"strategy": strategy, **params}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def grid_combos(
    strategy: str,
    grid: dict[str, list[Any]] | None = None,
) -> list[dict[str, Any]]:
    """展开网格为参数组合列表。"""
    g = grid if grid is not None else cast(dict[str, list[Any]], GRID_BENCH)
    keys = sorted(g.keys())
    out = []
    for vals in itertools.product(*[g[k] for k in keys]):
        out.append({"strategy": strategy, **dict(zip(keys, vals))})
    return out


_PARAMETER_KEYS = ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")


def _validated_combo_overrides(
    strategy: str,
    combos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize an explicitly preregistered combo list and reject ambiguity."""
    if not combos:
        raise ValueError("combos_override 不能为空")
    normalized: dict[str, dict[str, Any]] = {}
    for raw in combos:
        if not isinstance(raw, dict):
            raise TypeError("combos_override 每项必须为对象")
        raw_strategy = str(raw.get("strategy") or strategy)
        if raw_strategy != strategy:
            raise ValueError("combos_override 的 strategy 与研究任务不一致")
        missing = [key for key in _PARAMETER_KEYS if raw.get(key) is None]
        if missing:
            raise ValueError(f"combos_override 缺少参数: {missing}")
        combo = {
            "strategy": strategy,
            "vol_ratio_min": float(raw["vol_ratio_min"]),
            "strong_reset": int(raw["strong_reset"]),
            "exit_window": int(raw["exit_window"]),
            "stop_pct": float(raw["stop_pct"]),
        }
        normalized[param_id(strategy, combo)] = combo
    return [normalized[key] for key in sorted(normalized)]


def _detect_signals_for_code(
    df: pd.DataFrame,
    sample_days: list[str],
    cal_index: dict[str, int],
    cal: list[str],
    horizon: int,
    strategy: str,
    vr_levels: list[float],
    signal_kwargs: dict | None = None,
) -> list[dict]:
    """单只股票的信号缓存：逐采样日检测入场，返回信号列表。

    每项: {day, entry_i, bench_vols: {vr: bench_vol}}
    A 方案：detect_accumulation_breakout（与参数无关，detect 一次；signal_kwargs 可覆盖形态阈值）
    B 方案：detect_plan_b（vol_ratio_min 影响建仓识别，按 vr_levels 各 detect 一次）
    """
    from ab_screener.research.resilient_absorption import (
        BASE_ENTRY_MECHANISM_ID,
        evaluate_entry_mechanism,
        split_signal_kwargs,
    )
    from bench_volume import find_build_seqs
    from entry_plan_b import detect_plan_b
    from signals import detect_accumulation_breakout

    skwargs, entry_mechanism_id = split_signal_kwargs(signal_kwargs)
    if strategy != "A" and entry_mechanism_id != BASE_ENTRY_MECHANISM_ID:
        raise ValueError("韧性吸收机制只适用于 A 方案严格横盘突破")
    dts = df["trade_date"].astype(str).tolist()
    dts_set = set(dts)
    signals: list[dict] = []
    seen: set[tuple[str, float | None]] = set()
    first_sample = min(sample_days) if sample_days else ""
    last_sample = max(sample_days) if sample_days else ""

    def causal_window(breakout_day: str) -> pd.DataFrame:
        """只保留突破日当时可见的 K 线，禁止用后续站稳数据回填过去成交。"""
        breakout_i = cal_index.get(breakout_day, -1)
        if breakout_i < 0:
            return df.iloc[0:0]
        causal_start = cal[max(0, breakout_i - horizon)]
        return df[(df["trade_date"] >= causal_start) & (df["trade_date"] <= breakout_day)]

    for day in sample_days:
        day_i = cal_index.get(day, -1)
        if day_i < 60:
            continue
        win_start = cal[max(0, day_i - horizon)]
        win = df[(df["trade_date"] >= win_start) & (df["trade_date"] <= day)]
        if len(win) < 60:
            continue

        if strategy == "A":
            sig = detect_accumulation_breakout(win, **skwargs)
            if not sig.get("is_breakout"):
                continue
            bd = "".join(ch for ch in str(sig.get("breakout_date") or "") if ch.isdigit())[:8]
            recent = {str(x) for x in cal[max(0, day_i - 5) : day_i + 1]}
            if not bd or bd not in recent or bd not in dts_set or bd < first_sample or bd > last_sample:
                continue
            causal = causal_window(bd)
            causal_sig = detect_accumulation_breakout(causal, **skwargs)
            causal_bd = "".join(ch for ch in str(causal_sig.get("breakout_date") or "") if ch.isdigit())[:8]
            if not causal_sig.get("is_breakout") or causal_bd != bd:
                continue
            mechanism_evidence = evaluate_entry_mechanism(
                entry_mechanism_id,
                causal,
                causal_sig,
            )
            if not mechanism_evidence.get("passed"):
                continue
            key_a = (bd, None)
            if key_a in seen:
                continue
            entry_i = dts.index(bd)
            if entry_i + 1 >= len(df):
                continue
            bo_vol = float(df.loc[df["trade_date"] == bd, "vol"].iloc[0])
            bench_vols = {}
            for vr in vr_levels:
                seqs = find_build_seqs(causal, vol_ratio_min=vr)
                bench_vols[vr] = seqs[-1]["bench_vol"] if seqs else bo_vol
            seen.add(key_a)
            signals.append(
                {
                    "day": bd,
                    "discovered_on": day,
                    "entry_i": entry_i,
                    "bench_vols": bench_vols,
                    # 交易标注（回测工作台 K 线展示用）
                    "box_high": causal_sig.get("box_high"),
                    "box_low": causal_sig.get("box_low"),
                    "breakout_date": bd,
                    "entry_mechanism": mechanism_evidence,
                }
            )

        else:  # strategy B
            for vr in vr_levels:
                sig = detect_plan_b(win, vol_ratio_min=vr)
                if not sig.get("is_breakout"):
                    continue
                bd = "".join(ch for ch in str(sig.get("breakout_date") or "") if ch.isdigit())[:8]
                if bd not in dts_set or bd < first_sample or bd > last_sample:
                    continue
                causal = causal_window(bd)
                causal_sig = detect_plan_b(causal, vol_ratio_min=vr)
                causal_bd = "".join(ch for ch in str(causal_sig.get("breakout_date") or "") if ch.isdigit())[
                    :8
                ]
                if not causal_sig.get("is_breakout") or causal_bd != bd:
                    continue
                key_b = (bd, vr)
                if key_b in seen:
                    continue
                entry_i = dts.index(bd)
                if entry_i + 1 >= len(df):
                    continue
                seen.add(key_b)
                signals.append(
                    {
                        "day": bd,
                        "discovered_on": day,
                        "entry_i": entry_i,
                        "bench_vols": {vr: causal_sig["bench_vol"]},
                        "vr": vr,
                        "box_high": causal_sig.get("box_high"),
                        "box_low": causal_sig.get("box_low"),
                        "breakout_date": bd,
                    }
                )
    return signals


def _replay_params(df: pd.DataFrame, signals: list[dict], combos: list[dict]) -> dict[str, list[dict]]:
    """对缓存信号按 108 组出场参数重放模拟。返回 {pid: [trades]}。"""
    out: dict[str, list[dict]] = {}
    for combo in combos:
        pid = param_id(combo["strategy"], combo)
        trades = out.setdefault(pid, [])
        for s in signals:
            if combo["strategy"] == "B" and s.get("vr") != combo.get("vol_ratio_min"):
                continue  # B 方案信号与该 vr 档位绑定
            bv = s["bench_vols"].get(combo.get("vol_ratio_min"))
            if not bv:
                continue
            sim = simulate_trade(
                df,
                s["entry_i"],
                mode="bench",
                params={
                    "bench_vol": bv,
                    "stop_pct": combo["stop_pct"],
                    "exit_window": combo["exit_window"],
                    "strong_reset": combo["strong_reset"],
                },
            )
            if sim.get("ok"):
                # 交易标注（回测工作台 K 线展示用）：入场日=信号次日、出场日
                df_dates = df["date"].astype(str).tolist() if "date" in df.columns else []
                entry_date = (
                    df_dates[int(sim["entry_index"])]
                    if 0 <= int(sim["entry_index"]) < len(df_dates)
                    else None
                )
                exit_date = (
                    df_dates[int(sim["exit_index"])] if 0 <= int(sim["exit_index"]) < len(df_dates) else None
                )
                trades.append(
                    {
                        "ret": sim["ret"],
                        "win": sim["win"],
                        "exit": sim["exit"],
                        "days": sim["days"],
                        "max_dd": sim.get("max_dd"),
                        "entry": sim.get("entry"),
                        "exit_price": sim.get("exit_price"),
                        "ts_code": str(df["ts_code"].iloc[0]) if "ts_code" in df else "",
                        "date": s["day"],
                        "cost": cost_adjusted_trade(df, sim),
                        "entry_date": entry_date,
                        "exit_date": exit_date,
                        "box_high": s.get("box_high"),
                        "box_low": s.get("box_low"),
                        "breakout_date": s.get("breakout_date"),
                        "entry_mechanism": s.get("entry_mechanism"),
                    }
                )
    return out


def _worker_chunk(payload: tuple) -> dict[str, list[dict]]:
    """子进程：对股票分片跑「信号缓存 + 全参数重放」。

    payload 末尾两项：signal_kwargs（形态阈值）、costs（成本覆盖，子进程内临时生效）。
    """
    (
        codes,
        chunk_df,
        sample_days,
        cal,
        horizon,
        strategy,
        vr_levels,
        combos,
        signal_kwargs,
        costs,
        allowed_signal_dates,
    ) = payload
    if costs:
        from ab_screener.research.backtest_engine import apply_cost_override

        apply_cost_override(costs)
    try:
        cal_index = {d: i for i, d in enumerate(cal)}
        merged: dict[str, list[dict]] = {}
        for code in codes:
            df = chunk_df[chunk_df["ts_code"] == code].sort_values("trade_date").reset_index(drop=True)
            if len(df) < 80:
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            for col in ("open", "high", "low", "close"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["vol"] = pd.to_numeric(df.get("vol", df.get("volume")), errors="coerce")
            signals = _detect_signals_for_code(
                df,
                sample_days,
                cal_index,
                cal,
                horizon,
                strategy,
                vr_levels,
                signal_kwargs=signal_kwargs,
            )
            if allowed_signal_dates is not None:
                signals = [signal for signal in signals if signal.get("day") in allowed_signal_dates]
            if not signals:
                continue
            for pid, trades in _replay_params(df, signals, combos).items():
                merged.setdefault(pid, []).extend(trades)
        return merged
    finally:
        if costs:
            import ab_screener.domain.costs as C
            from ab_screener.domain.costs import COST_KEYS_DEFAULT

            C.COMMISSION_RATE = COST_KEYS_DEFAULT["commission_rate"]
            C.COMMISSION_MIN = COST_KEYS_DEFAULT["commission_min"]
            C.STAMP_TAX_SELL = COST_KEYS_DEFAULT["stamp_tax_sell"]
            C.OTHER_FEE_RATE = COST_KEYS_DEFAULT["other_fee_rate"]
            C.SLIPPAGE = COST_KEYS_DEFAULT["slippage"]


def run_grid(
    start: str,
    end: str,
    strategy: str = "A",
    step: int = 5,
    max_codes: int | None = None,
    horizon: int = 160,
    grid: dict | None = None,
    workers: int | None = None,
    progress_cb=None,
    cancel_check=None,
    signal_kwargs: dict | None = None,
    costs: dict | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    research_snapshot: ResearchPitSnapshot | None = None,
    combos_override: list[dict[str, Any]] | None = None,
    capture_formal_series: bool = False,
    allowed_signal_dates: frozenset[str] | set[str] | None = None,
) -> pd.DataFrame:
    """网格优化主入口。返回每组参数一行统计的 DataFrame（按 profit_factor 降序）。

    signal_kwargs：透传给 detect_accumulation_breakout 的形态阈值覆盖（自定义回测用）。
    costs：成本覆盖 {commission_rate, commission_min, stamp_tax_sell, other_fee_rate, slippage}，
           在 worker 进程内临时生效。
    """
    from local_store import LocalStore
    from parallel_scan import resolve_workers

    if capture_formal_series and portfolio_policy is None:
        raise ValueError("正式收益序列必须使用版本化组合账户模型")
    if _is_cancelled(cancel_check):
        raise ResearchCancelled("用户取消")
    # 加载区间前置扩展：箱体/建仓序列判定需要窗口前 horizon 日数据
    load_start = (pd.to_datetime(start) - pd.Timedelta(days=365)).strftime("%Y%m%d")
    if research_snapshot is not None:
        codes = list(research_snapshot.universe)
        if max_codes is not None:
            codes = codes[:max_codes]
        big = research_snapshot.load_daily(ts_codes=codes, start=load_start, end=end)
        cal = sorted(big["trade_date"].astype(str).unique().tolist())
    else:
        store = LocalStore()
        codes = research_universe(max_codes, include_delisted=True)
        # 兼容探索路径；权威研究必须显式传入冻结 PIT 快照。
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
        return pd.DataFrame()
    combos = (
        _validated_combo_overrides(strategy, combos_override)
        if combos_override is not None
        else grid_combos(strategy, grid)
    )
    vr_levels = sorted({c["vol_ratio_min"] for c in combos})
    normalized_allowed_dates = (
        frozenset(str(value)[:8] for value in allowed_signal_dates)
        if allowed_signal_dates is not None
        else None
    )
    if progress_cb:
        progress_cb(f"优化池 {len(codes)} 只 × {len(sample_days)} 采样日 × {len(combos)} 组合", 5)

    if big.empty:
        return pd.DataFrame()
    prepared_market = (
        prepare_portfolio_market(big, portfolio_policy) if portfolio_policy is not None else None
    )

    nw = resolve_workers(workers)
    results: dict[str, list[dict]] = {}
    if len(codes) < _MIN_CODES_FOR_POOL or nw <= 1:
        r = _worker_chunk(
            (
                codes,
                big,
                sample_days,
                cal,
                horizon,
                strategy,
                vr_levels,
                combos,
                signal_kwargs,
                costs,
                normalized_allowed_dates,
            )
        )
        results = r
        if _is_cancelled(cancel_check):
            raise ResearchCancelled("用户取消")
    else:
        chunk_size = max(50, (len(codes) + nw - 1) // nw)
        chunks = [codes[i : i + chunk_size] for i in range(0, len(codes), chunk_size)]
        pool = ProcessPoolExecutor(max_workers=nw)
        abandoned = False
        try:
            futs = [
                pool.submit(
                    _worker_chunk,
                    (
                        ch,
                        big[big["ts_code"].isin(ch)].copy(),
                        sample_days,
                        cal,
                        horizon,
                        strategy,
                        vr_levels,
                        combos,
                        signal_kwargs,
                        costs,
                        normalized_allowed_dates,
                    ),
                )
                for ch in chunks
            ]
            chunk_codes = {fut: ch for fut, ch in zip(futs, chunks)}
            results, _lost = _collect_pool_results(
                pool,
                set(futs),
                chunk_count=len(chunks),
                progress_cb=progress_cb,
                cancel_check=cancel_check,
                chunk_codes=chunk_codes,
            )
        except ResearchCancelled:
            abandoned = True
            raise
        finally:
            if not abandoned:
                pool.shutdown(wait=True)

    # 聚合统计
    rows = []
    combo_map = {param_id(c["strategy"], c): c for c in combos}
    for pid, trades in results.items():
        s = summarize(trades)
        if not s.get("n_trades"):
            continue
        trade_metrics = summarize_costed_trades(trades)
        row = {"param_id": pid, **combo_map[pid], **s, **trade_metrics}
        if portfolio_policy is not None and prepared_market is not None:
            portfolio = simulate_portfolio(
                trades,
                prepared_market,
                policy=portfolio_policy,
            )
            row.update({f"trade_{key}": value for key, value in trade_metrics.items()})
            row.update(portfolio_gate_metrics(portfolio))
            if capture_formal_series:
                dates = [str(item["trade_date"]) for item in portfolio["equity_curve"]]
                returns = [float(value) for value in portfolio["portfolio_daily_returns"]]
                if len(dates) != len(returns):
                    raise PortfolioAccountingError("组合日收益与权益日期长度不一致")
                row["_formal_daily_returns"] = dict(zip(dates, returns))
        rows.append(row)
    df_out = pd.DataFrame(rows)
    if not df_out.empty:
        df_out = df_out[df_out["net_n_trades"] >= BT_MIN_TRADES]  # 统计功效门槛按实际成交计
        sort_columns = ["net_profit_factor", "net_avg_return"]
        ascending = [False, False]
        if portfolio_policy is not None:
            df_out["_portfolio_complete"] = df_out["portfolio_status"].eq("PASS")
            sort_columns.insert(0, "_portfolio_complete")
            ascending.insert(0, False)
        df_out = (
            df_out.sort_values(sort_columns, ascending=ascending, na_position="last")
            .drop(columns=["_portfolio_complete"], errors="ignore")
            .reset_index(drop=True)
        )
    return df_out


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20250101")
    p.add_argument("--end", default="20260731")
    p.add_argument("--strategy", default="A", choices=["A", "B"])
    p.add_argument("--step", type=int, default=10)
    p.add_argument("--max-codes", type=int, default=200)
    args = p.parse_args()
    df = run_grid(
        start=args.start,
        end=args.end,
        strategy=args.strategy,
        step=args.step,
        max_codes=args.max_codes,
        progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}"),
    )
    pd.set_option("display.width", 200)
    print(df.head(15).to_string() if not df.empty else "无有效组合（样本不足或无信号）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
