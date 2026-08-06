"""样本内外切分 + Walk-forward 滚动复核（P4 验证层）

- IS/OOS：优化器只跑样本内，Top3 组合在样本外一次性验证（绝不反向调参）
- WF 复核：3 个滚动窗口（12 个月训练 / 6 个月测试，步进 6 个月），
  通过条件：OOS 平均 PF >= WF_MIN_OOS_PF_RATIO × IS PF，且每个测试窗 DD <= 25%
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import (  # noqa: E402
    BT_IS_END,
    BT_IS_START,
    BT_OOS_END,
    BT_OOS_START,
    WF_MIN_OOS_PF_RATIO,
)
from optimizer import param_id, run_grid  # noqa: E402

WF_WINDOWS = [
    # (train_start, train_end, test_start, test_end)
    ("20230801", "20240731", "20240801", "20250131"),
    ("20240201", "20250131", "20250201", "20250731"),
    ("20240801", "20250731", "20250801", "20260131"),
]


def split_windows() -> dict:
    return {"IS": (BT_IS_START, BT_IS_END), "OOS": (BT_OOS_START, BT_OOS_END)}


def _single_grid(combo: dict) -> dict:
    return {k: [combo[k]] for k in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}


def eval_combo(combo: dict, start: str, end: str, step: int = 5,
               max_codes: int | None = None, progress_cb=None) -> dict:
    """对单个参数组合在指定区间回测，返回统计行。"""
    df = run_grid(start=start, end=end, strategy=combo["strategy"], step=step,
                  max_codes=max_codes, grid=_single_grid(combo), progress_cb=progress_cb)
    if df.empty:
        return {"n_trades": 0}
    return df.iloc[0].to_dict()


def run_is_oos(strategy: str, step: int = 5, max_codes: int | None = None,
               top_n: int = 3, progress_cb=None,
               is_start: str = BT_IS_START, is_end: str = BT_IS_END,
               oos_start: str = BT_OOS_START, oos_end: str = BT_OOS_END) -> dict:
    """完整流程：IS 网格 → 过滤（胜率≥30%、DD≤25%）→ Top N → OOS 验证。

    窗口可用参数覆盖（数据不足时传较短窗口降级；默认按 config 24/12 月）。
    """
    is_df = run_grid(start=is_start, end=is_end, strategy=strategy, step=step,
                     max_codes=max_codes, progress_cb=progress_cb)
    if is_df.empty:
        return {"is": is_df, "oos": pd.DataFrame(), "msg": "样本内无有效组合"}
    elig = is_df[(is_df["win_rate"] >= 0.30) & (is_df["max_drawdown"] <= 0.25)]
    top = elig.head(top_n)
    oos_rows = []
    for _, row in top.iterrows():
        combo = {k: row[k] for k in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
        oos = eval_combo(combo, oos_start, oos_end, step=step,
                         max_codes=max_codes, progress_cb=progress_cb)
        oos_rows.append({**combo, **{f"oos_{k}": v for k, v in oos.items() if k not in combo},
                         **{f"is_{k}": row[k] for k in ("n_trades", "win_rate", "profit_factor", "max_drawdown")}})
    return {"is": is_df, "oos": pd.DataFrame(oos_rows)}


def wf_recheck(combos: list[dict], step: int = 5, max_codes: int | None = None,
               progress_cb=None, windows: list[tuple] | None = None) -> pd.DataFrame:
    """对 Top 组合做 3 窗口滚动复核，附 wf_pass 判定。windows 可覆盖（降级时传短窗）。"""
    wf_windows = windows or WF_WINDOWS
    rows = []
    for combo in combos:
        tests = []
        for wname, (ts, te, vs, ve) in zip(("WF1", "WF2", "WF3"), wf_windows):
            train = eval_combo(combo, ts, te, step=step, max_codes=max_codes)
            test = eval_combo(combo, vs, ve, step=step, max_codes=max_codes)
            tests.append({"window": wname, "train_pf": train.get("profit_factor"),
                          "test_pf": test.get("profit_factor"),
                          "test_dd": test.get("max_drawdown"),
                          "test_wr": test.get("win_rate"),
                          "test_n": test.get("n_trades", 0)})
        is_pf = tests[-1]["train_pf"]  # 最近窗口的训练 PF 作参照
        oos_pfs = [t["test_pf"] for t in tests if t.get("test_pf") is not None]
        oos_mean = sum(oos_pfs) / len(oos_pfs) if oos_pfs else None
        dd_ok = all((t.get("test_dd") or 0) <= 0.25 for t in tests)
        wf_pass = bool(oos_mean is not None and is_pf and oos_mean >= WF_MIN_OOS_PF_RATIO * is_pf and dd_ok)
        rows.append({**combo, "oos_mean_pf": round(oos_mean, 3) if oos_mean else None,
                     "wf_pass": wf_pass, "wf_detail": tests})
    return pd.DataFrame(rows)


def gap_check(min_dates: int = 240) -> dict:
    """日线覆盖检查（用于扩容验证；完整 3 年约 730 交易日）。"""
    from local_store import LocalStore

    dates = LocalStore().distinct_dates("daily")
    return {"n_dates": len(dates), "earliest": dates[0] if dates else None,
            "latest": dates[-1] if dates else None, "ok": len(dates) >= min_dates}


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--strategy", default="A", choices=["A", "B"])
    p.add_argument("--step", type=int, default=10)
    p.add_argument("--max-codes", type=int, default=200)
    args = p.parse_args()
    r = run_is_oos(strategy=args.strategy, step=args.step, max_codes=args.max_codes,
                   progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}"))
    pd.set_option("display.width", 220)
    print("=== 样本内 Top ===")
    print(r["is"].head(5).to_string() if not r["is"].empty else "(空)")
    print("=== 样本外验证 ===")
    print(r["oos"].to_string() if not r["oos"].empty else "(空)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
