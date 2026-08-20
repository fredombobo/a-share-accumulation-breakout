"""CLI：全市场结构扫描，输出 Top 结构候选（Phase 1）。

用法：
  python -m logic_platform.cli.run_logic_scan --limit 100 --top 10
  python -m logic_platform.cli.run_logic_scan --limit 5000 --top 20 --json out.json
  python -m logic_platform.cli.run_logic_scan --code 002030.SZ   # 单票详查

输出按状态优先级 + 箱体质量排序：
  BREAKOUT/FOLLOW_THROUGH > TIGHTENING > ACCUMULATION > 其他。
研究用途，非买卖建议（research_only）。
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from logic_platform.data.ab_store import ABStore

_ORDER = {"BREAKOUT": 0, "FOLLOW_THROUGH": 1, "TIGHTENING": 2,
          "ACCUMULATION": 3, "FAIL": 4, "IDLE": 5}


def _analyze_one(code: str) -> dict | None:
    """进程池 worker：单票状态分析（模块级函数以便 picklable）。"""
    try:
        from logic_platform.data.ab_store import ABStore
        from logic_platform.service import _analyze_raw

        store = ABStore(migrate=False)
        raw = _analyze_raw(code, store)
        if raw is None:
            return None
        rec = raw["record"]
        meta = store.stock_meta(code)
        box = rec.box or {}
        return {
            "ts_code": code,
            "name": (meta or {}).get("name"),
            "state": rec.state,
            "state_since": rec.state_since,
            "latest_close": round(float(raw["df"]["close"].iloc[-1]), 3),
            "box_amp": box.get("amp"),
            "box_days": box.get("days"),
            "box_high": box.get("high"),
            "box_low": box.get("low"),
            "breakout_date": rec.breakout_date,
            "breakout_vol_ratio": round(float(raw["sig"].get("breakout_vol_ratio") or 0), 2)
            if raw["sig"].get("breakout_vol_ratio") else None,
            "vol_percentile_60": round(float(
                raw["feats"]["vol_percentile_60"].iloc[-1]), 3)
            if raw["feats"]["vol_percentile_60"].notna().any() else None,
            "quality": box.get("quality"),
        }
    except Exception:  # noqa: BLE001 —— 单票失败不拖垮整批
        return None


def scan(limit: int = 100, market: str | None = None, workers: int = 4) -> list[dict]:
    store = ABStore()
    codes = store.universe_from_stock_basic(market=market)
    if limit and limit > 0:
        codes = codes[:limit]
    print(f"[scan] universe={len(codes)} 只，workers={workers}", file=sys.stderr)

    results: list[dict] = []
    if workers <= 1 or len(codes) <= 2:
        for i, c in enumerate(codes, 1):
            r = _analyze_one(c)
            if r:
                results.append(r)
            if i % 50 == 0:
                print(f"[scan] {i}/{len(codes)}", file=sys.stderr)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(_analyze_one, codes), 1):
                if r:
                    results.append(r)
                if i % 50 == 0:
                    print(f"[scan] {i}/{len(codes)}", file=sys.stderr)

    results.sort(key=lambda x: (_ORDER.get(x["state"], 9), -(x.get("quality") or 0)))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="量价结构扫描（研究用）")
    ap.add_argument("--limit", type=int, default=100, help="扫描上限（默认 100，全市场用 6000）")
    ap.add_argument("--market", default=None, help="市场过滤：主板/创业板/科创板 等")
    ap.add_argument("--top", type=int, default=10, help="输出 Top N")
    ap.add_argument("--workers", type=int, default=4, help="进程数")
    ap.add_argument("--json", default=None, help="结果 JSON 输出路径（可选）")
    ap.add_argument("--code", default=None, help="单票详查模式")
    args = ap.parse_args()

    if args.code:
        r = _analyze_one(args.code)
        if not r:
            print(f"无数据: {args.code}")
            return 1
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    t0 = datetime.now()
    results = scan(limit=args.limit, market=args.market, workers=args.workers)
    top = results[: args.top]

    print(f"\n[result] Top {len(top)}（扫描 {len(results)} 有结构票，耗时 {(datetime.now()-t0).total_seconds():.0f}s）")
    header = f"{'代码':<12}{'名称':<10}{'状态':<16}{'收盘':>8}{'箱体振幅':>10}{'箱体天数':>8}{'量能分位':>10}"
    print(header)
    print("-" * len(header))
    for r in top:
        amp_str = f"{r['box_amp']*100:.1f}%" if r.get("box_amp") is not None else "-"
        vol_str = (f"{r['vol_percentile_60']:.2f}"
                   if r.get("vol_percentile_60") is not None else "-")
        print(
            f"{r['ts_code']:<12}{(r.get('name') or ''):<10}{r['state']:<16}"
            f"{r['latest_close']:>8.2f}{amp_str:>10}{(r.get('box_days') or 0):>8}{vol_str:>10}"
        )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"generated_at": t0.isoformat(), "results": results}, fh,
                      ensure_ascii=False, indent=2)
        print(f"\n[json] 已写 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
