"""样本内外切分 + Walk-forward 滚动复核（P4 验证层）

- IS/OOS：优化器只跑样本内，Top3 组合在样本外一次性验证（绝不反向调参）
- WF 复核：3 个滚动窗口（12 个月训练 / 6 个月测试，步进 6 个月），
  通过条件：OOS 平均 PF >= WF_MIN_OOS_PF_RATIO × IS PF，且每个测试窗 DD <= 25%
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import (
    BT_IS_END,
    BT_IS_START,
    BT_OOS_END,
    BT_OOS_START,
    WF_MIN_OOS_PF_RATIO,
)
from optimizer import run_grid

if TYPE_CHECKING:
    from ab_screener.research.portfolio_accounting import PortfolioPolicy

WF_WINDOWS = [
    # (train_start, train_end, test_start, test_end)
    ("20230801", "20240731", "20240801", "20250131"),
    ("20240201", "20250131", "20250201", "20250731"),
    ("20240801", "20250731", "20250801", "20260131"),
]

# IS 网格入选过滤门限（自定义回测 CLI 共用同一口径）
ELIG_MIN_NET_WIN_RATE = 0.30
ELIG_MAX_NET_DRAWDOWN = 0.25


def split_windows() -> dict:
    return {"IS": (BT_IS_START, BT_IS_END), "OOS": (BT_OOS_START, BT_OOS_END)}


def _single_grid(combo: dict) -> dict:
    return {k: [combo[k]] for k in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}


def _phase_progress(progress_cb, label: str, start: int, span: int):
    if progress_cb is None:
        return None

    def report(message: str, progress: int) -> None:
        bounded = max(0, min(int(progress), 100))
        progress_cb(f"{label} · {message}", start + int(span * bounded / 100))

    return report


def eval_combo(
    combo: dict,
    start: str,
    end: str,
    step: int = 5,
    max_codes: int | None = None,
    progress_cb=None,
    cancel_check=None,
    signal_kwargs: dict | None = None,
    costs: dict | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
) -> dict:
    """对单个参数组合在指定区间回测，返回统计行。"""
    df = run_grid(
        start=start,
        end=end,
        strategy=combo["strategy"],
        step=step,
        max_codes=max_codes,
        grid=_single_grid(combo),
        progress_cb=progress_cb,
        cancel_check=cancel_check,
        signal_kwargs=signal_kwargs,
        costs=costs,
        portfolio_policy=portfolio_policy,
    )
    if df.empty:
        return {"n_trades": 0}
    return df.iloc[0].to_dict()


def run_is_oos(
    strategy: str,
    step: int = 5,
    max_codes: int | None = None,
    top_n: int = 3,
    progress_cb=None,
    is_start: str = BT_IS_START,
    is_end: str = BT_IS_END,
    oos_start: str = BT_OOS_START,
    oos_end: str = BT_OOS_END,
    grid: dict | None = None,
    single: dict | None = None,
    signal_kwargs: dict | None = None,
    costs: dict | None = None,
    cancel_check=None,
    portfolio_policy: PortfolioPolicy | None = None,
) -> dict:
    """完整流程：IS 网格 → 过滤（胜率≥30%、DD≤25%）→ Top N → OOS 验证。

    - grid: 自定义网格（如只勾选部分量比档）；None 用 config.GRID_BENCH
    - single: 单组 what-if，跳过网格展开，直接 IS+OOS 各评一次（便于人工调参）
    - signal_kwargs: 透传给 detect_accumulation_breakout 的形态阈值（自定义回测用）
    - costs: 成本覆盖（回测工作台用）
    """
    if single:
        combo = {
            "strategy": strategy,
            "vol_ratio_min": float(single["vol_ratio_min"]),
            "strong_reset": int(single["strong_reset"]),
            "exit_window": int(single["exit_window"]),
            "stop_pct": float(single["stop_pct"]),
        }
        is_row = eval_combo(
            combo,
            is_start,
            is_end,
            step=step,
            max_codes=max_codes,
            progress_cb=_phase_progress(progress_cb, "IS", 0, 50),
            cancel_check=cancel_check,
            signal_kwargs=signal_kwargs,
            costs=costs,
            portfolio_policy=portfolio_policy,
        )
        is_df = pd.DataFrame([{**combo, **is_row}]) if is_row.get("n_trades") else pd.DataFrame()
        oos = eval_combo(
            combo,
            oos_start,
            oos_end,
            step=step,
            max_codes=max_codes,
            progress_cb=_phase_progress(progress_cb, "OOS", 50, 50),
            cancel_check=cancel_check,
            signal_kwargs=signal_kwargs,
            costs=costs,
            portfolio_policy=portfolio_policy,
        )
        oos_df = pd.DataFrame(
            [
                {
                    **combo,
                    **{f"oos_{k}": v for k, v in oos.items() if k not in combo},
                    **{
                        f"is_{k}": is_row.get(k)
                        for k in (
                            "n_trades",
                            "win_rate",
                            "profit_factor",
                            "max_drawdown",
                            "net_n_trades",
                            "net_win_rate",
                            "net_profit_factor",
                            "net_max_drawdown",
                        )
                    },
                }
            ]
        )
        if progress_cb:
            progress_cb("单组试跑完成", 100)
        return {"is": is_df, "oos": oos_df, "msg": None, "mode": "single"}

    is_df = run_grid(
        start=is_start,
        end=is_end,
        strategy=strategy,
        step=step,
        max_codes=max_codes,
        grid=grid,
        progress_cb=_phase_progress(progress_cb, "IS", 0, 50),
        cancel_check=cancel_check,
        signal_kwargs=signal_kwargs,
        costs=costs,
        portfolio_policy=portfolio_policy,
    )
    if is_df.empty:
        return {"is": is_df, "oos": pd.DataFrame(), "msg": "样本内无有效组合", "mode": "grid"}
    eligibility = (
        (is_df["net_win_rate"] >= ELIG_MIN_NET_WIN_RATE)
        & (is_df["net_max_drawdown"] <= ELIG_MAX_NET_DRAWDOWN)
        & (is_df["net_profit_factor"].notna())
    )
    if portfolio_policy is not None:
        eligibility &= is_df["portfolio_status"].eq("PASS")
    elig = is_df[eligibility]
    top = elig.head(top_n) if not elig.empty else is_df.head(top_n)
    oos_rows = []
    total = max(1, len(top))
    for index, (_, row) in enumerate(top.iterrows()):
        combo = {k: row[k] for k in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
        start_progress = 50 + int(50 * index / total)
        span = max(1, int(50 * (index + 1) / total) - int(50 * index / total))
        oos = eval_combo(
            combo,
            oos_start,
            oos_end,
            step=step,
            max_codes=max_codes,
            progress_cb=_phase_progress(progress_cb, f"OOS {index + 1}/{total}", start_progress, span),
            cancel_check=cancel_check,
            signal_kwargs=signal_kwargs,
            costs=costs,
            portfolio_policy=portfolio_policy,
        )
        oos_rows.append(
            {
                **combo,
                **{f"oos_{k}": v for k, v in oos.items() if k not in combo},
                **{
                    f"is_{k}": row[k]
                    for k in (
                        "n_trades",
                        "win_rate",
                        "profit_factor",
                        "max_drawdown",
                        "net_n_trades",
                        "net_win_rate",
                        "net_profit_factor",
                        "net_max_drawdown",
                    )
                },
            }
        )
    return {"is": is_df, "oos": pd.DataFrame(oos_rows), "mode": "grid"}


def wf_recheck(
    combos: list[dict],
    step: int = 5,
    max_codes: int | None = None,
    progress_cb=None,
    windows: list[tuple] | None = None,
    cancel_check=None,
    signal_kwargs: dict | None = None,
    costs: dict | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
) -> pd.DataFrame:
    """对 Top 组合做 3 窗口滚动复核，附 wf_pass 判定。windows 可覆盖（降级时传短窗）。"""
    wf_windows = windows or WF_WINDOWS
    rows = []
    total_evaluations = max(1, len(combos) * len(wf_windows) * 2)
    completed_evaluations = 0
    for combo in combos:
        tests = []
        for wname, (ts, te, vs, ve) in zip(("WF1", "WF2", "WF3"), wf_windows):
            if progress_cb:
                progress_cb(f"{wname} 训练窗", int(100 * completed_evaluations / total_evaluations))
            train = eval_combo(
                combo,
                ts,
                te,
                step=step,
                max_codes=max_codes,
                cancel_check=cancel_check,
                signal_kwargs=signal_kwargs,
                costs=costs,
                portfolio_policy=portfolio_policy,
            )
            completed_evaluations += 1
            if progress_cb:
                progress_cb(f"{wname} 测试窗", int(100 * completed_evaluations / total_evaluations))
            test = eval_combo(
                combo,
                vs,
                ve,
                step=step,
                max_codes=max_codes,
                cancel_check=cancel_check,
                signal_kwargs=signal_kwargs,
                costs=costs,
                portfolio_policy=portfolio_policy,
            )
            completed_evaluations += 1
            if progress_cb:
                progress_cb(f"{wname} 完成", int(100 * completed_evaluations / total_evaluations))
            tests.append(
                {
                    "window": wname,
                    "train_pf": train.get("net_profit_factor"),
                    "test_pf": test.get("net_profit_factor"),
                    "test_dd": test.get("net_max_drawdown"),
                    "test_wr": test.get("net_win_rate"),
                    "test_n": test.get("net_n_trades", 0),
                    "train_portfolio_status": train.get("portfolio_status"),
                    "test_portfolio_status": test.get("portfolio_status"),
                }
            )
        evidence_complete = len(tests) == 3 and all(
            t.get(key) is not None for t in tests for key in ("train_pf", "test_pf", "test_dd", "test_n")
        )
        if portfolio_policy is not None:
            evidence_complete = evidence_complete and all(
                t.get("train_portfolio_status") == "PASS" and t.get("test_portfolio_status") == "PASS"
                for t in tests
            )
        train_pfs = [float(t["train_pf"]) for t in tests] if evidence_complete else []
        test_pfs = [float(t["test_pf"]) for t in tests] if evidence_complete else []
        train_mean = sum(train_pfs) / len(train_pfs) if train_pfs else None
        test_mean = sum(test_pfs) / len(test_pfs) if test_pfs else None
        dd_ok = evidence_complete and all(float(t["test_dd"]) <= 0.25 for t in tests)
        trades_ok = evidence_complete and all(int(t["test_n"]) >= 30 for t in tests)
        ratio_ok = bool(
            evidence_complete
            and train_mean is not None
            and train_mean > 0
            and test_mean is not None
            and test_mean >= WF_MIN_OOS_PF_RATIO * train_mean
        )
        wf_pass = bool(evidence_complete and dd_ok and trades_ok and ratio_ok)
        rows.append(
            {
                **combo,
                "train_mean_pf": round(train_mean, 3) if train_mean is not None else None,
                "oos_mean_pf": round(test_mean, 3) if test_mean is not None else None,
                "evidence_complete": evidence_complete,
                "wf_pass": wf_pass,
                "wf_detail": tests,
            }
        )
    return pd.DataFrame(rows)


def gap_check(min_dates: int = 240) -> dict:
    """日线覆盖检查（用于扩容验证；完整 3 年约 730 交易日）。"""
    from local_store import LocalStore

    dates = LocalStore().distinct_dates("daily")
    return {
        "n_dates": len(dates),
        "earliest": dates[0] if dates else None,
        "latest": dates[-1] if dates else None,
        "ok": len(dates) >= min_dates,
    }


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--strategy", default="A", choices=["A", "B"])
    p.add_argument("--step", type=int, default=10)
    p.add_argument("--max-codes", type=int, default=200)
    args = p.parse_args()
    r = run_is_oos(
        strategy=args.strategy,
        step=args.step,
        max_codes=args.max_codes,
        progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}"),
    )
    pd.set_option("display.width", 220)
    print("=== 样本内 Top ===")
    print(r["is"].head(5).to_string() if not r["is"].empty else "(空)")
    print("=== 样本外验证 ===")
    print(r["oos"].to_string() if not r["oos"].empty else "(空)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
