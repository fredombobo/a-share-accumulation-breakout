"""
扫描内核 —— 编排（进程/取消/进度/排序/聚合）
============================================
职责：run_scan 主体 —— 加载数据 → 预过滤 → 信号检测 → 打分 → 阶梯 →
观察池 → 拆池 → 交易卡片 → 导出 → 持久化。负责 worker 解析、取消、
进度回调、排序与聚合；不直接做单标的的信号/评分计算。

ENTRY、评分公式、阈值、默认参数、结果格式与历史 run_screener 完全一致。
store / as_of 为确定性回归与内核注入使用的可选后门：
  - store=None 时行为与旧版完全一致（默认生产库 / data_fetch 模块级读取）
  - store 注入时所有只读与持久化都路由到该 store（测试冻结市场）
  - as_of 提供时把 latest_date 钉在该日期并截断 trade_dates/daily（防未来泄漏）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import config as _cfg
import data_fetch
from ab_screener.screener.data_loader import load_market_data
from ab_screener.screener.evaluator import (
    _detect_on_codes,
    _score_codes,
    _theme_soft_fill,
    apply_box_ladder,
)
from ab_screener.screener.prefilter import prefilter
from charting import plot_top_kline_batch
from config import (
    BUILD_WATCH_POOL,
    FUND_FLOW_DAYS,
    FUND_FLOW_MIN_RATIO,
    HORIZON_DAYS,
    INCLUDE_RELAXED_IN_A,
    RELAXED_FUND_FLOW_MIN_RATIO,
    REQUIRED_THEMES,
    TARGET_SELECT_COUNT,
    THEME_MIN_PER_SECTOR,
    TOP_N,
    TOP_N_TRADE,
    TOP_N_WATCH,
)
from config import (
    CACHE_DIR as CACHE_DIR_STR,
)
from config import (
    OUT_DIR as OUT_DIR_STR,
)
from market_regime import data_freshness, detect_regime
from parallel_scan import prefilter_volume_parallel, resolve_workers
from pool_select import split_pools
from sector_themes import annotate_themes
from trade_plan import attach_trade_cards

# 兼容旧进程缓存的 config（热更新前无此字段）
SCAN_WORKERS = int(getattr(_cfg, "SCAN_WORKERS", 0) or 0)

CACHE_DIR = Path(CACHE_DIR_STR)
OUT_DIR = Path(OUT_DIR_STR)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_THEME_MIN = {t: THEME_MIN_PER_SECTOR for t in REQUIRED_THEMES}


def _resolve_store(store):
    """store=None 时返回默认生产库 LocalStore（延迟 import 防循环）。"""
    if store is not None:
        return store
    from local_store import LocalStore
    return LocalStore()


def run_scan(
    top: int = TOP_N,
    days: int = HORIZON_DAYS,
    force: bool = False,
    max_check: int | None = None,
    progress_cb=None,
    build_watch: bool | None = None,
    include_relaxed_in_a: bool | None = None,
    workers: int | None = None,
    cancel_check=None,
    *,
    store=None,
    as_of: str = "",
) -> dict:
    """主扫描：A 池(strict 可交易) + B 池(观察，可选 theme_fill)。

    默认 top = A 池数量（15）。theme_fill 永不混入 A 池。
    cancel_check：返回 True 时在阶段间停止扫描（在不可中断的并行阶段内于分片粒度生效）。
    store：LocalStore 兼容只读/持久化注入（None=默认生产库）。
    as_of：钉死扫描基准日（YYYYMMDD）；空串=由数据自动推导。
    """
    def _prog(stage: str, pct: int, msg: str = "") -> None:
        if progress_cb:
            try:
                progress_cb(stage, pct, msg)
            except Exception:  # noqa: BLE001
                pass
        if msg:
            print(f"[{stage}] {msg}")

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    def _stop_if_cancelled(stage: str) -> bool:
        """返回 True 表示已取消，调用方应立即 return。"""
        if _cancelled():
            _prog(stage, 0, "扫描已取消")
            return True
        return False

    def _cancelled_result(regime_obj=None) -> dict:
        if regime_obj is not None and hasattr(regime_obj, "to_dict"):
            reg = regime_obj.to_dict()
        elif isinstance(regime_obj, dict):
            reg = regime_obj
        else:
            reg = {}
        return {
            "cancelled": True,
            "latest_date": "",
            "total_candidates": 0,
            "hits": [],
            "df": pd.DataFrame(),
            "df_a": pd.DataFrame(),
            "df_b": pd.DataFrame(),
            "sig": {},
            "kline_dfs": {},
            "chart_paths": {},
            "elapsed_sec": round(time.time() - t0, 1),
            "out_xlsx": "",
            "quota_report": {},
            "regime": reg,
            "freshness": {},
            "pool_report": {},
            "workers": n_workers,
        }

    build_watch = BUILD_WATCH_POOL if build_watch is None else build_watch
    include_relaxed_in_a = INCLUDE_RELAXED_IN_A if include_relaxed_in_a is None else include_relaxed_in_a
    top_a = top or TOP_N_TRADE
    n_workers = resolve_workers(SCAN_WORKERS if workers is None else workers)

    t0 = time.time()
    if _stop_if_cancelled("启动"):
        return _cancelled_result()

    _prog("数据准备", 6, "加载本地行情…")
    try:
        basic, trade_dates, daily, dbbasic, _ = load_market_data(
            days, force, db_path=store.db_path if store is not None else None,
        )
    except Exception as e:
        _prog("数据准备", 6, f"加载失败: {str(e)[:80]}")
        raise
    if _stop_if_cancelled("数据准备"):
        return _cancelled_result()

    if daily is None or getattr(daily, "empty", True):
        _prog("数据准备", 6, "日线为空，无法扫描")
        return {
            **_cancelled_result(),
            "cancelled": False,
            "msg": "empty_daily",
        }

    # as_of 钉死：截断日线/trade_dates，防止未来数据进入确定性回归
    if as_of:
        _a = str(as_of)[:8]
        daily = daily[daily["trade_date"].astype(str) <= _a] if daily is not None and not daily.empty else daily
        trade_dates = [d for d in trade_dates if str(d) <= _a] or trade_dates

    if daily is not None and not daily.empty and "trade_date" in daily.columns:
        max_d = str(pd.to_numeric(daily["trade_date"], errors="coerce").max()).split(".")[0]
        if max_d and max_d != "nan":
            max_d = max_d.zfill(8) if max_d.isdigit() else max_d
            trade_dates = [d for d in trade_dates if str(d) <= max_d] or trade_dates
    latest_date = trade_dates[-1] if trade_dates else ""
    if daily is not None and not daily.empty:
        latest_date = str(max(daily["trade_date"].astype(str).unique()))
    if dbbasic is None or getattr(dbbasic, "empty", True) or "ts_code" not in getattr(dbbasic, "columns", []):
        try:
            if store is not None:
                dbbasic = store.load_daily_basic(start=latest_date, end=latest_date)
            else:
                dbbasic = data_fetch.get_daily_basic_by_dates([latest_date], sleep=0.2)
        except Exception:  # noqa: BLE001
            pass

    # 市场环境（优先 000300.SH）
    if store is not None:
        _store_ref = store
        regime = detect_regime(store=_store_ref, daily=daily)
    else:
        try:
            from local_store import LocalStore
            _store_ref = LocalStore()
            regime = detect_regime(store=_store_ref, daily=daily)
        except Exception:  # noqa: BLE001
            _store_ref = None
            regime = detect_regime(daily=daily)

    fresh = data_freshness(
        latest_date,
        trade_dates=trade_dates if trade_dates else None,
        store=_store_ref,
    )
    _prog(
        "数据准备",
        8,
        f"最新交易日 {latest_date}  新鲜度={fresh['label']}({fresh.get('stale_label', fresh['stale_days'])})",
    )
    _prog("环境", 12, f"{regime.label}({regime.index_code}) 开仓={regime.allow_new_entries} 名额≤{regime.max_trade_slots}")

    if _stop_if_cancelled("预过滤"):
        return _cancelled_result(regime)
    cand = prefilter(basic, dbbasic)
    _prog("数据准备", 15, f"预过滤后候选 {len(cand)} 只")
    if max_check and max_check < len(cand):
        cand = cand.head(max_check)
    if _stop_if_cancelled("预过滤"):
        return _cancelled_result(regime)

    # 排序可能较慢：前后都查取消
    _prog("数据准备", 16, "整理日线排序…")
    if _stop_if_cancelled("数据准备"):
        return _cancelled_result(regime)
    daily_sorted = daily.sort_values(["ts_code", "trade_date"])
    if _stop_if_cancelled("数据准备"):
        return _cancelled_result(regime)

    sig_by_code: dict[str, dict] = {}
    all_codes = set(cand["ts_code"].tolist()) if cand is not None and not cand.empty else set()
    if not all_codes:
        _prog("数据准备", 17, "候选为空")
        return {
            "cancelled": False,
            "latest_date": latest_date,
            "total_candidates": 0,
            "hits": [],
            "df": pd.DataFrame(),
            "df_a": pd.DataFrame(),
            "df_b": pd.DataFrame(),
            "sig": {},
            "kline_dfs": {},
            "chart_paths": {},
            "elapsed_sec": round(time.time() - t0, 1),
            "out_xlsx": "",
            "quota_report": {},
            "regime": regime.to_dict() if hasattr(regime, "to_dict") else {},
            "freshness": fresh if isinstance(fresh, dict) else {},
            "pool_report": {},
            "workers": n_workers,
        }

    # 量能预筛：多进程粗筛，加速 strict 全市场扫描（可取消，不再卡死）
    _prog("预筛", 18, f"量能/近高点粗筛 {len(all_codes)} 只（workers={n_workers}）…")
    if _stop_if_cancelled("预筛"):
        return _cancelled_result(regime)
    fast_codes = prefilter_volume_parallel(
        daily_sorted,
        all_codes,
        workers=n_workers,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )
    if _stop_if_cancelled("预筛"):
        return _cancelled_result(regime)
    if len(fast_codes) < 50:
        # 预筛过狠则回退全量
        fast_codes = all_codes
        _prog("预筛", 19, "预筛过少，回退全量扫描")
    else:
        _prog("预筛", 20, f"预筛保留 {len(fast_codes)} 只（砍掉 {len(all_codes)-len(fast_codes)}）")

    _prog("信号检测", 22, f"严格参数扫描 {len(fast_codes)} 只 ×{n_workers} 核…")
    hit_codes = _detect_on_codes(
        fast_codes, daily_sorted, sig_by_code,
        relaxed=False, workers=n_workers, progress_cb=progress_cb, cancel_check=cancel_check,
    )
    if _stop_if_cancelled("信号检测"):
        return _cancelled_result(regime)
    _prog("信号检测", 50, f"严格命中 {len(hit_codes)} 只")

    hit_dates = trade_dates[-FUND_FLOW_DAYS:] if trade_dates else []
    _prog("资金流", 55, f"拉取近 {FUND_FLOW_DAYS} 日资金流…")
    if store is not None:
        mf = store.load_moneyflow(start=hit_dates[0], end=hit_dates[-1]) if hit_dates else pd.DataFrame()
    else:
        mf = data_fetch.get_moneyflow_by_dates(hit_dates, sleep=0.2) if hit_dates else pd.DataFrame()
    if _stop_if_cancelled("资金流"):
        return _cancelled_result(regime)
    mf_by_code = {code: g for code, g in mf.groupby("ts_code")} if not mf.empty else {}

    dbbasic_latest = dbbasic[dbbasic["trade_date"] == latest_date] if "trade_date" in getattr(dbbasic, "columns", []) else dbbasic
    if dbbasic_latest is None or getattr(dbbasic_latest, "empty", True):
        basic_latest = basic.copy()
    else:
        basic_latest = basic.merge(dbbasic_latest, on="ts_code", how="inner")

    _prog("综合打分", 60, "A池 strict 打分（资金质量+新鲜度）…")
    rows = _score_codes(
        hit_codes, sig_by_code, basic_latest, mf_by_code,
        fund_min_ratio=FUND_FLOW_MIN_RATIO, tier="strict",
        latest_date=latest_date, require_breakout=True, require_fund_quality=True,
        trade_dates=trade_dates,
    )

    # 横盘阶梯：先只要 ~6 个月，不够再 5→4→… 直到凑满 TARGET_SELECT_COUNT
    target_n = max(top_a, TARGET_SELECT_COUNT)
    rows, ladder_rep = apply_box_ladder(rows, target=target_n)
    _prog(
        "箱体阶梯",
        65,
        f"strict 阶梯 min_days={ladder_rep.get('ladder_min_days')} "
        f"≈{ladder_rep.get('months_approx')}月 保留 {ladder_rep.get('kept')}/{target_n} "
        f"tried={ladder_rep.get('tried')}",
    )
    df_all = pd.DataFrame(rows)

    # B 池：若 strict 阶梯后仍不足目标，用 relaxed 补量再跑阶梯；theme_fill 仅观察
    if build_watch:
        need_more = len(df_all) < target_n
        already = set(df_all["ts_code"].tolist()) if not df_all.empty else set()
        # relaxed 仍必须具备近期放量或贴近高点；复用更宽松的预筛集合，
        # 避免在 Windows 上对已明确不具备启动特征的全市场再次完整扫描。
        relax_pool = set(fast_codes) - already
        _prog("观察池", 70, f"relaxed 扫描 {len(relax_pool)} 只 ×{n_workers} 核…")
        new_hits = _detect_on_codes(
            relax_pool, daily_sorted, sig_by_code,
            relaxed=True, workers=n_workers, progress_cb=progress_cb, cancel_check=cancel_check,
        )
        if _stop_if_cancelled("观察池"):
            return _cancelled_result(regime)
        extra = _score_codes(
            new_hits, sig_by_code, basic_latest, mf_by_code,
            fund_min_ratio=RELAXED_FUND_FLOW_MIN_RATIO, tier="relaxed",
            latest_date=latest_date, require_breakout=True, require_fund_quality=False,
            trade_dates=trade_dates,
        )
        if need_more and extra:
            # 不足目标：strict+relaxed 合并后再阶梯，优先长横盘
            merged_rows = (df_all.to_dict("records") if not df_all.empty else []) + extra
            merged_rows, ladder_rep2 = apply_box_ladder(merged_rows, target=target_n)
            _prog(
                "箱体阶梯",
                72,
                f"strict+relaxed 阶梯 min_days={ladder_rep2.get('ladder_min_days')} "
                f"保留 {ladder_rep2.get('kept')}/{target_n}",
            )
            ladder_rep = {**ladder_rep, "after_relaxed": ladder_rep2}
            df_all = pd.DataFrame(merged_rows)
        elif extra:
            df_all = (
                pd.concat([df_all, pd.DataFrame(extra)], ignore_index=True)
                .drop_duplicates("ts_code", keep="first")
                if not df_all.empty
                else pd.DataFrame(extra)
            )

        # theme_fill 仅补 B 池
        theme_min = dict(_DEFAULT_THEME_MIN)
        ann = annotate_themes(df_all) if not df_all.empty else df_all
        shortfall = []
        if not ann.empty and "主题列表" in ann.columns:
            for th in REQUIRED_THEMES:
                n = int(ann["主题列表"].astype(str).str.contains(th, na=False).sum())
                if n < 3:
                    shortfall.append(th)
        if shortfall:
            _prog("观察池", 80, f"theme_fill 补观察主题 {shortfall}…")
            fill_rows = _theme_soft_fill(
                shortfall_themes=shortfall,
                need_total=TOP_N_WATCH,
                theme_min=theme_min,
                cand=cand,
                daily_sorted=daily_sorted,
                basic_latest=basic_latest,
                mf_by_code=mf_by_code,
                mf_dates=hit_dates,
                already=set(df_all["ts_code"].tolist()) if not df_all.empty else set(),
                sig_by_code=sig_by_code,
            )
            if fill_rows:
                df_all = pd.concat([df_all, pd.DataFrame(fill_rows)], ignore_index=True).drop_duplicates("ts_code", keep="first")

    # 拆池：A 池目标 top_a（默认 20）；防守期仍清空可交易名额
    slots = regime.max_trade_slots if regime.allow_new_entries else 0
    if _stop_if_cancelled("拆池"):
        return _cancelled_result(regime)
    if not regime.allow_new_entries:
        _prog("环境", 85, "防守环境：A 池清空（禁止新开仓）；结果仍写入 B/观察供回看")
    a_df, b_df, pool_report = split_pools(
        df_all if not df_all.empty else pd.DataFrame(),
        top_a=top_a,
        top_b=TOP_N_WATCH,
        include_relaxed_in_a=include_relaxed_in_a,
        regime_max_slots=slots if regime.allow_new_entries else 0,
    )
    pool_report["box_ladder"] = ladder_rep

    # 交易卡片
    a_df = attach_trade_cards(a_df, regime=regime.regime, sig_by_code=sig_by_code)
    b_df = attach_trade_cards(b_df, regime=regime.regime, sig_by_code=sig_by_code)

    # 默认输出 A 池；合并导出时 A 在前
    top_df = a_df.copy() if a_df is not None and not a_df.empty else pd.DataFrame()
    export_df = pd.concat([a_df, b_df], ignore_index=True) if build_watch else a_df

    print(f"\nA池(可交易)={len(a_df)}  B池(观察)={len(b_df)}  环境={regime.label}")
    print(f"池报告: {pool_report}")

    # K 线仅 A 池
    kline_dfs: dict[str, pd.DataFrame] = {}
    chart_paths: dict = {}
    if not top_df.empty:
        if _stop_if_cancelled("K线图"):
            return _cancelled_result(regime)
        _prog("K线图", 90, f"生成 A 池 {len(top_df)} 张…")
        for code in top_df["ts_code"].tolist():
            g = daily_sorted[daily_sorted["ts_code"] == code].copy()
            g["date"] = pd.to_datetime(g["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            kline_dfs[code] = g
        chart_paths = plot_top_kline_batch(top_df.to_dict("records"), kline_dfs, sig_by_code)
        top_df = top_df.copy()
        top_df["K线图"] = top_df["ts_code"].map(chart_paths)

    # 写 SQLite：A+B，reasons 带池标记
    try:
        write_store = _resolve_store(store)
        scan_rows = []
        for pool_name, part in (("A", a_df), ("B", b_df)):
            if part is None or part.empty:
                continue
            for _, r in part.iterrows():
                sig = sig_by_code.get(r["ts_code"]) or {}
                scan_rows.append({
                    "trade_date": latest_date,
                    "ts_code": r["ts_code"],
                    "name": r["名称"],
                    "industry": r["行业"],
                    "price": r["最新价"],
                    "mv_yi": r.get("总市值(亿)"),
                    "pe": r.get("PE(TTM)"),
                    "pb": r.get("PB"),
                    "turnover": r.get("换手率%"),
                    "box_days": r.get("箱体天数"),
                    "box_amp": r.get("箱体振幅%"),
                    "vol_ratio": r.get("量比"),
                    "fund_net_wan": r.get("主力净流入(万)"),
                    "fund_ratio": r.get("净流入/成交额%"),
                    "signal_score": r.get("信号强度分"),
                    "fund_score": r.get("资金流分"),
                    "basic_score": r.get("基本面分"),
                    "total_score": r.get("综合分"),
                    "reasons": f"[池{pool_name}|{r.get('筛选层级','')}|{r.get('主题板块','')}] {r.get('入选理由','')}",
                    "breakout_date": r.get("突破日"),
                    # 信号字段持久化：总览直接读表，避免每次请求重算 detect（30 只 ~4s）
                    "box_high": sig.get("box_high"),
                    "box_low": sig.get("box_low"),
                    "ma5": sig.get("ma5"),
                    "ma20": sig.get("ma20"),
                    "sig_calculated": 1,
                })
        if scan_rows:
            # 先清当日再写，避免旧 theme_fill 残留主导排序
            try:
                with write_store._connect() as conn:
                    conn.execute("DELETE FROM scan_result WHERE trade_date=?", (latest_date,))
            except Exception:  # noqa: BLE001
                pass
            write_store.upsert_scan_result(pd.DataFrame(scan_rows))
            # 快照清理：仅保留最近 10 个交易日的扫描快照（历史快照用于审计，避免无限积累）
            try:
                with write_store._connect() as conn:
                    keep = conn.execute(
                        "SELECT DISTINCT trade_date FROM scan_result ORDER BY trade_date DESC LIMIT 10"
                    ).fetchall()
                    keep_dates = [str(r[0]) for r in keep]
                    if keep_dates:
                        ph = ",".join("?" * len(keep_dates))
                        conn.execute(
                            f"DELETE FROM scan_result WHERE trade_date NOT IN ({ph})",
                            keep_dates,
                        )
            except Exception:  # noqa: BLE001
                pass
            print(f"✅ 已写入 SQLite scan_result: {len(scan_rows)} 条 (A={len(a_df)} B={len(b_df)})")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] scan_result 写入失败: {str(e)[:100]}")

    out_xlsx = OUT_DIR / f"accumulation_breakout_A{len(a_df)}_B{len(b_df)}_{latest_date}.xlsx"
    if export_df is not None and not export_df.empty:
        # 去掉不可序列化列
        exp = export_df.drop(columns=[c for c in ("_trade_card",) if c in export_df.columns], errors="ignore")
        exp.to_excel(out_xlsx, index=False)
    report = {
        "pool": pool_report,
        "regime": regime.to_dict(),
        "freshness": fresh,
        "a_count": len(a_df),
        "b_count": len(b_df),
    }
    report_path = OUT_DIR / f"scan_report_{latest_date}.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    _prog("完成", 100, f"A={len(a_df)} B={len(b_df)} 环境={regime.label}")
    print(f"\n✅ 已导出: {out_xlsx}")

    return {
        "latest_date": latest_date,
        "total_candidates": len(df_all) if df_all is not None else 0,
        "hits": hit_codes,
        "df": top_df,  # A 池主输出
        "df_a": a_df,
        "df_b": b_df,
        "sig": sig_by_code,
        "kline_dfs": kline_dfs,
        "chart_paths": chart_paths,
        "elapsed_sec": round(time.time() - t0, 1),
        "out_xlsx": str(out_xlsx),
        "quota_report": report,  # 兼容旧字段名
        "regime": regime.to_dict(),
        "freshness": fresh,
        "pool_report": pool_report,
        "workers": n_workers,
    }
