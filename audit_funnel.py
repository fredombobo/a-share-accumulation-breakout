"""全市场筛选漏斗审计：量化每层淘汰了多少票。"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from config import FUND_FLOW_MIN_RATIO, FUND_POSITIVE_DAYS_MIN
from local_store import LocalStore
from market_regime import detect_regime
from pool_select import fund_flow_quality_ok
from prefilter_fast import volume_breakout_candidates
from run_screener import prefilter
from scoring import calc_fund_flow_strength, fundamental_filter_passes
from signals import detect_accumulation_breakout


def main() -> int:
    store = LocalStore()
    basic = store.load_stock_basic()
    md = store.max_trade_date("daily")
    d1 = store.load_daily(start=md, end=md)

    print("=== 数据底座 ===")
    print(f"stock_basic: {len(basic)}")
    print(f"daily max: {md}")
    print(f"当日日线股票数: {d1['ts_code'].nunique() if d1 is not None and not d1.empty else 0}")

    caches = sorted(Path("out/cache").glob("market_160d_*.pkl"))
    if caches:
        basic2, trade_dates, daily, dbbasic, _mf = pd.read_pickle(caches[-1])
        print(f"cache: {caches[-1].name}")
        print(f"  basic={len(basic2)} dates={len(trade_dates)} {trade_dates[0]}~{trade_dates[-1]}")
        print(f"  daily rows={len(daily)} codes={daily['ts_code'].nunique()}")
    else:
        cal = store.distinct_dates("daily", limit=160)
        trade_dates = cal
        daily = store.load_daily(start=cal[0], end=cal[-1])
        dbbasic = store.load_daily_basic(start=cal[-1], end=cal[-1])
        basic2 = basic
        print(f"store load: dates={len(cal)} daily codes={daily['ts_code'].nunique()}")

    # 若 cache 的 as_of 落后于 DB，优先用 DB 最新 160 日（审计更真实）
    if str(trade_dates[-1]) < str(md):
        print(f"[warn] cache 截止 {trade_dates[-1]} < DB {md}，改用 DB 最近 160 日")
        cal = store.distinct_dates("daily", limit=160)
        trade_dates = cal
        daily = store.load_daily(start=cal[0], end=cal[-1])
        dbbasic = store.load_daily_basic(start=cal[-1], end=cal[-1])
        basic2 = basic

    cand = prefilter(basic2, dbbasic)
    all_codes = set(cand["ts_code"].tolist())
    daily_sorted = daily.sort_values(["ts_code", "trade_date"])
    fast = volume_breakout_candidates(daily_sorted, all_codes)

    print("\n=== 漏斗 ===")
    print(f"1 stock_basic/列表:     {len(basic2):5d}")
    print(f"2 prefilter(ST/次新/价/市值): {len(cand):5d}  砍掉 {len(basic2) - len(cand)}")
    print(f"3 量能/近高点预筛:      {len(fast):5d}  砍掉 {len(all_codes) - len(fast)}")

    codes = list(fast)
    print(f"\nstrict 检测中… {len(codes)} 只")
    t0 = time.time()
    hits: list[str] = []
    fail_reasons: dict[str, int] = {}
    grp = daily_sorted.groupby("ts_code")
    for i, code in enumerate(codes):
        try:
            g = grp.get_group(code).copy()
        except KeyError:
            continue
        g["date"] = pd.to_datetime(g["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        sig = detect_accumulation_breakout(g)
        if sig.get("is_breakout"):
            hits.append(code)
        else:
            r0 = (sig.get("reasons") or ["?"])[0]
            r0 = str(r0)[:48]
            fail_reasons[r0] = fail_reasons.get(r0, 0) + 1
        if (i + 1) % 400 == 0:
            print(f"  {i+1}/{len(codes)} hits={len(hits)} {time.time()-t0:.0f}s")

    print(f"4 strict 突破命中:      {len(hits):5d}  ({time.time()-t0:.0f}s)")
    print("  未命中主因 Top10:")
    for k, v in sorted(fail_reasons.items(), key=lambda x: -x[1])[:10]:
        print(f"    {v:5d}  {k}")

    latest = str(trade_dates[-1])
    mf_dates = [str(x) for x in trade_dates[-5:]]
    mf = store.load_moneyflow(start=mf_dates[0], end=mf_dates[-1])
    mf_by = {c: g for c, g in mf.groupby("ts_code")} if mf is not None and not mf.empty else {}

    db_latest = dbbasic
    if dbbasic is not None and not dbbasic.empty and "trade_date" in dbbasic.columns:
        db_latest = dbbasic[dbbasic["trade_date"].astype(str) == latest]
    if db_latest is None or getattr(db_latest, "empty", True):
        basic_latest = basic2.copy()
    else:
        basic_latest = basic2.merge(db_latest, on="ts_code", how="inner")

    fail_basic = fail_ratio = fail_days = pass_all = 0
    for code in hits:
        meta = basic_latest[basic_latest["ts_code"] == code]
        if meta.empty:
            fail_basic += 1
            continue
        m = meta.iloc[0]
        mv = pd.to_numeric(m.get("total_mv"), errors="coerce")
        mv_yi = float(mv) / 10000.0 if pd.notna(mv) else None
        fund_row = pd.Series(
            {
                "name": m.get("name", ""),
                "pe": pd.to_numeric(m.get("pe"), errors="coerce"),
                "pb": pd.to_numeric(m.get("pb"), errors="coerce"),
                "total_mv_yi": mv_yi,
                "turnover_rate": pd.to_numeric(m.get("turnover_rate"), errors="coerce"),
                "close": pd.to_numeric(m.get("close"), errors="coerce"),
                "list_date": str(m.get("list_date", "")),
            }
        )
        ok, _ = fundamental_filter_passes(fund_row)
        if not ok:
            fail_basic += 1
            continue
        mf_rows = mf_by.get(code)
        _net, _score, ratio = calc_fund_flow_strength(mf_rows)
        if ratio < FUND_FLOW_MIN_RATIO:
            fail_ratio += 1
            continue
        qok, _ = fund_flow_quality_ok(mf_rows, min_positive_days=FUND_POSITIVE_DAYS_MIN)
        if not qok:
            fail_days += 1
            continue
        pass_all += 1

    print(f"5 基本面硬过滤失败:    {fail_basic:5d}")
    print(f"6 资金流 ratio 失败:    {fail_ratio:5d}")
    print(f"7 资金正向天数失败:    {fail_days:5d}")
    print(f"8 STRICT 可进 A 池候选: {pass_all:5d}")

    regime = detect_regime(store=store, daily=daily)
    print("\n=== 市场环境 ===")
    print(f"regime={regime.regime} label={regime.label}")
    print(f"allow_new_entries={regime.allow_new_entries} max_slots={regime.max_trade_slots}")
    print(f"notes={regime.notes}")
    if not regime.allow_new_entries:
        print(">>> 防守期：即使有 strict 命中，A 池也会被清空为 0")

    # 关键瓶颈标记
    print("\n=== 瓶颈判断 ===")
    if len(fast) < len(all_codes) * 0.5:
        print(f"- 量能预筛砍掉 {len(all_codes)-len(fast)}/{len(all_codes)} ({100*(len(all_codes)-len(fast))/max(len(all_codes),1):.0f}%)，可能漏掉「刚启动但末日未放量」票")
    if len(hits) < 30:
        print(f"- strict 突破过严：仅 {len(hits)} 只命中（突破窗5日+量比1.6+涨幅2~9.5%）")
    if fail_days > pass_all:
        print(f"- 资金正向天数门槛过严：淘汰 {fail_days}，通过 {pass_all}")
    if not regime.allow_new_entries:
        print("- 防守环境把 A 池强制清空（用户看到 0 或很少）")
    print("- B 池 relaxed 仅随机抽最多 800 只未命中票，不是全市场放宽扫描")
    print("- 默认输出只展示 A 池 Top15，不是全市场命中列表")

    conn = sqlite3.connect("runtime/stock_data.db")
    try:
        sc = pd.read_sql(
            "SELECT trade_date, COUNT(*) AS c FROM scan_result "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 8",
            conn,
        )
        print("\n=== scan_result 历史 ===")
        print(sc.to_string(index=False) if not sc.empty else "(空)")
        if not sc.empty:
            latest_scan = str(sc.iloc[0]["trade_date"])
            top = pd.read_sql(
                "SELECT ts_code, name, total_score, box_days, vol_ratio, reasons "
                "FROM scan_result WHERE trade_date=? ORDER BY total_score DESC LIMIT 15",
                conn,
                params=(latest_scan,),
            )
            print(f"\n最新扫描日 {latest_scan} Top15:")
            print(top.to_string(index=False))
    except Exception as e:  # noqa: BLE001
        print("scan_result 读取失败:", e)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
