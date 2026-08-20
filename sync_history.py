"""历史日线扩容（P0）

把本地 SQLite 日线从 ~300 交易日滚动窗口扩展到 3 年（~730 交易日），
供策略优化闭环的 24 个月样本内 + 12 个月样本外验证使用。

复用 local_store.sync_from_tushare 的差集断点续传逻辑：
- daily / daily_basic：拉取 HISTORY_SYNC_DAYS 交易日
- moneyflow：只保持最近 300 天窗口（历史资金流对 MVP 无用，省 API 配额）

用法：
  python sync_history.py                # 扩容 + 验证
  python sync_history.py --check-only   # 只做缺口验证
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import HISTORY_SYNC_DAYS
from local_store import LocalStore, sync_from_tushare


def gap_check(min_dates: int = 720) -> dict:
    """验证日线覆盖：distinct 日期数与最早/最晚日期。"""
    store = LocalStore()
    dates = store.distinct_dates("daily")
    n = len(dates)
    index = store.load_daily(ts_codes=["000300.SH"])
    index_latest = (
        str(index["trade_date"].astype(str).max())
        if index is not None and not index.empty and "trade_date" in index.columns
        else None
    )
    latest = dates[-1] if dates else None
    return {
        "n_dates": n,
        "earliest": dates[0] if dates else None,
        "latest": latest,
        "index_code": "000300.SH",
        "index_latest": index_latest,
        "index_ok": bool(latest and index_latest == latest),
        "ok": n >= min_dates and bool(latest and index_latest == latest),
    }


def backfill_daily(
    days_back: int = HISTORY_SYNC_DAYS,
    moneyflow_days: int = 300,
    verbose: bool = True,
    fetch_workers: int = 8,
) -> dict:
    """扩容主入口。差集逻辑保证可重复执行（中断后重跑自动续传）。"""
    if verbose:
        print(f"[backfill] 目标 daily {days_back} 交易日 / moneyflow {moneyflow_days} 交易日")
    result = sync_from_tushare(
        days_back=days_back,
        moneyflow_days=moneyflow_days,
        force=False,
        verbose=verbose,
        fetch_workers=fetch_workers,
    )
    # 风控环境基准独立来自 index_daily；股票 daily 不包含指数。
    from market_regime import ensure_index_daily

    index = ensure_index_daily(
        LocalStore(),
        index_code="000300.SH",
        days=max(120, days_back),
        allow_network=True,
    )
    result["index_rows"] = 0 if index is None else len(index)
    check = gap_check()
    if verbose:
        print(f"[backfill] 完成: daily_dates={len(result.get('daily_dates', []))} 新增, "
              f"库内共 {check['n_dates']} 日 ({check['earliest']}~{check['latest']}), "
              f"index={check['index_latest']}, ok={check['ok']}")
    return {"sync": result, "check": check}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=HISTORY_SYNC_DAYS)
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    if args.check_only:
        print(gap_check())
        from research_windows import recommend_research_plan

        plan = recommend_research_plan()
        print({"research_mode": plan.mode, "can_claim_edge": plan.can_claim_edge,
               "is": f"{plan.is_start}~{plan.is_end}", "oos": f"{plan.oos_start}~{plan.oos_end}"})
        return 0
    # Token 预检：失败则立即退出，避免空跑数小时
    from research_windows import probe_tushare_token

    tok = probe_tushare_token()
    if not tok.get("ok"):
        print(f"[sync_history] Token 不可用: {tok.get('error')}")
        print("  请到 https://tushare.pro 个人中心复制 token，写入 .env 的 TUSHARE_TOKEN 后重试。")
        print("  也可先: python research_status.py")
        return 3
    backfill_daily(days_back=args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
