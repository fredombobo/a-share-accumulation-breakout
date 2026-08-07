"""
全市场扫描主流程（胜率优先）
================
流程：
  1. 同步/加载本地 SQLite 行情
  2. 市场环境（进攻/中性/防守）
  3. 预过滤 + strict 信号 + 资金质量 + 突破新鲜度 → A 池（默认 Top15）
  4. 可选 B 池观察（relaxed / theme_fill，永不混入 A）
  5. 交易卡片（止损/目标/仓位）+ 导出

用法：
  python run_screener.py --top 15 --days 160
  python run_screener.py --top 15 --no-watch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

import data_fetch  # noqa: E402
from charting import plot_top_kline_batch  # noqa: E402
import config as _cfg  # noqa: E402
from config import (  # noqa: E402
    BUILD_WATCH_POOL,
    CACHE_DIR as CACHE_DIR_STR,
    FUND_FLOW_DAYS,
    FUND_FLOW_MIN_RATIO,
    FUND_POSITIVE_DAYS_MIN,
    HORIZON_DAYS,
    INCLUDE_RELAXED_IN_A,
    OUT_DIR as OUT_DIR_STR,
    RELAXED_BOX_MAX_AMP,
    RELAXED_BOX_MAX_MID_DRAWDOWN,
    RELAXED_BREAKOUT_CHG_MAX,
    RELAXED_BREAKOUT_CHG_MIN,
    RELAXED_BREAKOUT_VOL_RATIO,
    RELAXED_BREAKOUT_WINDOW_DAYS,
    RELAXED_FUND_FLOW_MIN_RATIO,
    REQUIRED_THEMES,
    THEME_MIN_PER_SECTOR,
    TOP_N,
    TOP_N_TRADE,
    TOP_N_WATCH,
)

# 兼容旧进程缓存的 config（热更新前无此字段）
SCAN_WORKERS = int(getattr(_cfg, "SCAN_WORKERS", 0) or 0)
BOX_LADDER_DAYS = tuple(getattr(_cfg, "BOX_LADDER_DAYS", (125, 105, 84, 63, 42, 20)))
TARGET_SELECT_COUNT = int(getattr(_cfg, "TARGET_SELECT_COUNT", 20) or 20)
from market_regime import data_freshness, detect_regime  # noqa: E402
from pool_select import (  # noqa: E402
    breakout_freshness_bonus,
    fund_flow_quality_ok,
    split_pools,
)
from scoring import (  # noqa: E402
    calc_fund_flow_strength,
    fund_positive_days,
    fundamental_filter_passes,
    is_delisted_name,
    is_st_name,
)
from parallel_scan import detect_many, prefilter_volume_parallel, resolve_workers  # noqa: E402
from sector_themes import match_themes, theme_universe_mask  # noqa: E402
from signals import detect_accumulation_breakout  # noqa: E402
from trade_plan import attach_trade_cards  # noqa: E402

from pathlib import Path
CACHE_DIR = Path(CACHE_DIR_STR)
OUT_DIR = Path(OUT_DIR_STR)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_THEME_MIN = {t: THEME_MIN_PER_SECTOR for t in REQUIRED_THEMES}


def load_market_data(days: int, force: bool = False) -> tuple[pd.DataFrame, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """加载全市场数据。返回 (stock_basic, trade_dates, daily_df, daily_basic_df, moneyflow_df)"""
    cache_key = f"market_{days}d_{datetime.now().strftime('%Y%m%d')}.pkl"
    cache_path = CACHE_DIR / cache_key

    if cache_path.exists() and not force:
        print(f"[cache] 使用缓存: {cache_path}")
        with open(cache_path, "rb") as f:
            return pd.read_pickle(f)  # type: ignore[return-value]

    # 最近交易日
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    cal = data_fetch.get_trade_cal(start, end)
    trade_dates = cal[-days:]

    print(f"[1/4] 拉取股票列表…")
    basic = data_fetch.get_stock_basic()

    print(f"[2/4] 拉取 {len(trade_dates)} 个交易日全市场日线（{trade_dates[0]} ~ {trade_dates[-1]}）…")
    daily = data_fetch.get_daily_by_dates(trade_dates, sleep=0.2)
    print(f"      日线行数: {len(daily)}")

    print(f"[3/4] 拉取全市场基本面指标…")
    dbbasic = data_fetch.get_daily_basic_by_dates(trade_dates[-1:], sleep=0.2)
    print(f"      基本面行数: {len(dbbasic)}")

    # 预过滤后先不拉资金流（只对信号命中者拉，省时间）
    mf = pd.DataFrame()

    payload = (basic, trade_dates, daily, dbbasic, mf)
    with open(cache_path, "wb") as f:
        pd.to_pickle(payload, f)
    print(f"[cache] 已保存: {cache_path}")
    return payload


def apply_box_ladder(
    rows: list[dict],
    *,
    target: int | None = None,
    ladder: tuple[int, ...] | None = None,
) -> tuple[list[dict], dict]:
    """横盘时长阶梯：优先 ≥6 个月，不足 target 只则 5→4→…→1 月。

    返回 (筛选后的 rows, 报告)。
    """
    target = int(target if target is not None else TARGET_SELECT_COUNT)
    ladder = tuple(ladder or BOX_LADDER_DAYS)
    if not rows:
        return [], {"ladder_min_days": None, "kept": 0, "target": target, "step": "empty"}

    def _days(r: dict) -> int:
        try:
            return int(r.get("箱体天数") or r.get("box_days") or 0)
        except (TypeError, ValueError):
            return 0

    def _score(r: dict) -> float:
        try:
            return float(r.get("综合分") or r.get("total_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    report: dict = {"target": target, "ladder": list(ladder), "tried": []}
    chosen: list[dict] = []
    chosen_min = ladder[-1] if ladder else 20
    for min_d in ladder:
        kept = [r for r in rows if _days(r) >= min_d]
        report["tried"].append({"min_days": min_d, "count": len(kept)})
        if len(kept) >= target:
            chosen = kept
            chosen_min = min_d
            break
        # 记录当前最宽可用集，若最终仍不足则用最宽松一档
        if not chosen or len(kept) >= len(chosen):
            chosen = kept
            chosen_min = min_d

    if not chosen:
        chosen = list(rows)
        chosen_min = 0

    # 同档内：分高优先，其次箱体更长
    chosen = sorted(chosen, key=lambda r: (_score(r), _days(r)), reverse=True)
    report.update({
        "ladder_min_days": chosen_min,
        "kept": len(chosen),
        "months_approx": round(chosen_min / 21, 1) if chosen_min else 0,
        "step": f">={chosen_min}d",
    })
    return chosen, report


def prefilter(basic: pd.DataFrame, dbbasic: pd.DataFrame) -> pd.DataFrame:
    """剔除 ST/退市/次新/无数据，返回候选 ts_code 列表"""
    if basic is None or basic.empty or "ts_code" not in basic.columns:
        return pd.DataFrame()
    # daily_basic 可能因日历超前/缺数为空：退化为只基于 stock_basic 过滤
    if dbbasic is None or getattr(dbbasic, "empty", True) or "ts_code" not in getattr(dbbasic, "columns", []):
        df = basic.copy()
    else:
        df = basic.merge(dbbasic, on="ts_code", how="inner")
    mask = pd.Series(True, index=df.index)
    mask &= ~df["name"].map(is_st_name)
    mask &= ~df["name"].map(is_delisted_name)

    # 次新股过滤（上市未满1年）
    list_dates = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
    mask &= (datetime.now() - list_dates).dt.days >= 250

    # 价格/市值粗筛
    if "close" in df.columns:
        mask &= pd.to_numeric(df["close"], errors="coerce").fillna(0) >= 3.0
    if "total_mv" in df.columns:
        mv_yi = pd.to_numeric(df["total_mv"], errors="coerce").fillna(0) / 10000.0
        mask &= mv_yi.between(20, 4000)

    out = df.loc[mask].copy()
    return out


def _score_codes(
    codes: list[str],
    sig_by_code: dict[str, dict],
    basic_latest: pd.DataFrame,
    mf_by_code: dict[str, pd.DataFrame],
    fund_min_ratio: float,
    tier: str = "strict",
    latest_date: str = "",
    require_breakout: bool = True,
    require_fund_quality: bool = True,
    trade_dates: list[str] | None = None,
) -> list[dict]:
    """对命中信号的代码做基本面+资金流+综合打分，返回行 dict 列表。"""
    from scoring import build_master_score
    from strategy_store import active_weights

    # 策略实验室回灌权重：取当前 active 参数的最高样本外 PF 作为通用排序权重
    _w = active_weights()
    param_weight_by_tier = {}
    if _w:
        w = max(float(v) for v in _w.values() if v and v > 0)
        param_weight_by_tier = {"strict": w, "relaxed": w}

    rows: list[dict] = []
    for code in codes:
        sig = sig_by_code.get(code)
        if not sig:
            continue
        if require_breakout and not sig.get("is_breakout"):
            continue
        row_meta = basic_latest[basic_latest["ts_code"] == code]
        if row_meta.empty:
            continue
        meta = row_meta.iloc[0]
        mv_yi = (
            pd.to_numeric(meta.get("total_mv"), errors="coerce") / 10000.0
            if pd.notna(meta.get("total_mv"))
            else None
        )
        fund_row = pd.Series({
            "name": meta.get("name", ""),
            "pe": pd.to_numeric(meta.get("pe"), errors="coerce"),
            "pb": pd.to_numeric(meta.get("pb"), errors="coerce"),
            "total_mv_yi": mv_yi,
            "turnover_rate": pd.to_numeric(meta.get("turnover_rate"), errors="coerce"),
            "close": pd.to_numeric(meta.get("close"), errors="coerce"),
            "list_date": str(meta.get("list_date", "")),
        })

        ok, _fails = fundamental_filter_passes(fund_row)
        if not ok:
            continue

        mf_rows = mf_by_code.get(code)
        fund_net, fund_score, fund_ratio = calc_fund_flow_strength(mf_rows)
        if fund_ratio < fund_min_ratio:
            continue
        pos_days = fund_positive_days(mf_rows)
        if require_fund_quality and tier == "strict":
            q_ok, _ = fund_flow_quality_ok(mf_rows, min_positive_days=FUND_POSITIVE_DAYS_MIN)
            if not q_ok:
                continue

        total, detail = build_master_score(sig, fund_score, fund_net, fund_ratio, fund_row,
                                           param_weight=param_weight_by_tier.get(tier, 1.0))
        fresh = breakout_freshness_bonus(
            sig.get("breakout_date"), latest_date, trade_dates=trade_dates,
        )
        total = round(max(0.0, min(100.0, total + fresh)), 1)
        industry = meta.get("industry", "")
        name = fund_row["name"]
        themes = match_themes(industry, name)
        reason = "；".join(sig.get("reasons") or [])
        if tier != "strict":
            reason = f"[{tier}]" + reason
        if fresh:
            reason += f"；新鲜度{fresh:+.0f}"

        rows.append({
            "ts_code": code,
            "代码": code.split(".")[0].zfill(6),
            "名称": name,
            "最新价": round(fund_row["close"], 2) if pd.notna(fund_row["close"]) else None,
            "行业": industry,
            "主题板块": themes[0] if themes else "其他",
            "主题列表": ",".join(themes) if themes else "其他",
            "总市值(亿)": round(mv_yi, 1) if mv_yi else None,
            "PE(TTM)": round(fund_row["pe"], 2) if pd.notna(fund_row["pe"]) else None,
            "PB": round(fund_row["pb"], 2) if pd.notna(fund_row["pb"]) else None,
            "换手率%": round(fund_row["turnover_rate"], 2) if pd.notna(fund_row["turnover_rate"]) else None,
            "箱体天数": sig["box_days"],
            "箱体振幅%": round(sig["box_amp"] * 100, 1) if sig.get("box_amp") is not None else None,
            "量比": round(sig["breakout_vol_ratio"], 2) if sig.get("breakout_vol_ratio") else None,
            "突破日涨幅%": round(sig["breakout_pct_chg"] * 100, 2) if sig.get("breakout_pct_chg") else None,
            "主力净流入(万)": round(fund_net, 0),
            "净流入/成交额%": round(fund_ratio * 100, 3),
            "资金正向天数": pos_days,
            "信号强度分": detail["信号强度分"],
            "资金流分": detail["资金流分"],
            "基本面分": detail["基本面分"],
            "综合分": total,
            "入选理由": reason,
            "突破日": sig.get("breakout_date"),
            "筛选层级": tier,
        })
    return rows


def _soft_setup_row(
    code: str,
    g2: pd.DataFrame,
    meta: pd.Series,
    mf_rows: pd.DataFrame | None,
    theme: str,
) -> dict | None:
    """主题强制补齐：不要求完整突破，按箱体质量+贴近上沿+资金流打软分。"""
    from scoring import build_master_score, score_fundamentals

    sig = detect_accumulation_breakout(
        g2,
        box_max_amp=0.45,
        breakout_vol_ratio=1.05,
        breakout_chg_min=0.005,
        breakout_chg_max=0.15,
        breakout_window_days=15,
    )
    mv_yi = (
        pd.to_numeric(meta.get("total_mv"), errors="coerce") / 10000.0
        if pd.notna(meta.get("total_mv"))
        else None
    )
    fund_row = pd.Series({
        "name": meta.get("name", ""),
        "pe": pd.to_numeric(meta.get("pe"), errors="coerce"),
        "pb": pd.to_numeric(meta.get("pb"), errors="coerce"),
        "total_mv_yi": mv_yi,
        "turnover_rate": pd.to_numeric(meta.get("turnover_rate"), errors="coerce"),
        "close": pd.to_numeric(meta.get("close"), errors="coerce"),
        "list_date": str(meta.get("list_date", "")),
    })
    ok, _ = fundamental_filter_passes(fund_row)
    if not ok:
        # 主题强制：仅放宽 PE 上限到 120，其余仍过滤 ST/退市/低价
        name = str(fund_row.get("name", ""))
        if is_st_name(name) or is_delisted_name(name):
            return None
        if pd.notna(fund_row.get("close")) and fund_row["close"] < 2.0:
            return None

    fund_net, fund_score, fund_ratio = calc_fund_flow_strength(mf_rows)
    # 软分：箱体 + 贴近上沿 + 资金 + 基本面
    soft = 0.0
    reasons = list(sig.get("reasons") or [])
    amp = sig.get("box_amp")
    if amp is not None and amp <= 0.45:
        soft += max(0.0, 35.0 * (1.0 - amp / 0.45))
        reasons.append(f"软箱体振幅{amp:.1%}")
    box_high = sig.get("box_high")
    last_c = sig.get("latest_close") or fund_row.get("close")
    if box_high and last_c and box_high > 0:
        prox = float(last_c) / float(box_high)
        if 0.90 <= prox <= 1.12:
            soft += 30.0 * (1.0 - abs(prox - 1.0) / 0.12)
            reasons.append(f"贴近箱顶{prox:.2f}")
    if sig.get("is_breakout"):
        soft += 25.0
        reasons.append("放宽后仍满足突破")
    soft += min(25.0, fund_score * 0.25)
    basic_score, _ = score_fundamentals(fund_row)
    soft += basic_score * 0.15
    # 若完全无箱体信息则用近20日位置
    if soft < 15 and g2 is not None and len(g2) >= 20:
        closes = pd.to_numeric(g2["close"], errors="coerce").dropna()
        if len(closes) >= 20:
            tail = closes.tail(20)
            lo, hi = float(tail.min()), float(tail.max())
            if hi > lo:
                pos = (float(tail.iloc[-1]) - lo) / (hi - lo)
                soft += 20.0 * pos
                reasons.append(f"20日位置{pos:.0%}")

    total = round(min(99.0, soft), 1)
    industry = meta.get("industry", "")
    name = fund_row["name"]
    return {
        "ts_code": code,
        "代码": code.split(".")[0].zfill(6),
        "名称": name,
        "最新价": round(float(fund_row["close"]), 2) if pd.notna(fund_row["close"]) else None,
        "行业": industry,
        "主题板块": theme,
        "主题列表": ",".join(match_themes(industry, name)) or theme,
        "总市值(亿)": round(mv_yi, 1) if mv_yi else None,
        "PE(TTM)": round(fund_row["pe"], 2) if pd.notna(fund_row["pe"]) else None,
        "PB": round(fund_row["pb"], 2) if pd.notna(fund_row["pb"]) else None,
        "换手率%": round(fund_row["turnover_rate"], 2) if pd.notna(fund_row["turnover_rate"]) else None,
        "箱体天数": sig.get("box_days") or 0,
        "箱体振幅%": round(sig["box_amp"] * 100, 1) if sig.get("box_amp") is not None else None,
        "量比": round(sig["breakout_vol_ratio"], 2) if sig.get("breakout_vol_ratio") else None,
        "突破日涨幅%": round(sig["breakout_pct_chg"] * 100, 2) if sig.get("breakout_pct_chg") else None,
        "主力净流入(万)": round(fund_net, 0),
        "净流入/成交额%": round(fund_ratio * 100, 3),
        "信号强度分": round(soft * 0.6, 1),
        "资金流分": fund_score,
        "基本面分": basic_score,
        "综合分": total,
        "入选理由": f"[主题强制/{theme}]" + "；".join(reasons[:4]),
        "突破日": sig.get("breakout_date"),
        "筛选层级": "theme_fill",
    }


def _theme_soft_fill(
    *,
    shortfall_themes: list[str],
    need_total: int,
    theme_min: dict[str, int],
    cand: pd.DataFrame,
    daily_sorted: pd.DataFrame,
    basic_latest: pd.DataFrame,
    mf_by_code: dict[str, pd.DataFrame],
    mf_dates: list[str],
    already: set[str],
    sig_by_code: dict[str, dict],
) -> list[dict]:
    """对缺口主题从板块宇宙中软评分补齐。"""
    rows: list[dict] = []
    grp = daily_sorted.groupby("ts_code")
    basic_idx = basic_latest.set_index("ts_code", drop=False)

    # 确保资金流覆盖主题池（批量已在库中）
    if not mf_by_code:
        mf = data_fetch.get_moneyflow_by_dates(mf_dates, sleep=0.15)
        if not mf.empty:
            mf_by_code.update({c: g for c, g in mf.groupby("ts_code")})

    for theme in shortfall_themes:
        mask = theme_universe_mask(cand, [theme])
        pool = [
            c for c in cand.loc[mask, "ts_code"].tolist()
            if c not in already
        ]
        scored: list[dict] = []
        for code in pool:
            if code not in grp.groups and code not in daily_sorted["ts_code"].values:
                continue
            try:
                g = grp.get_group(code)
            except KeyError:
                continue
            if code not in basic_idx.index:
                continue
            meta = basic_idx.loc[code]
            if isinstance(meta, pd.DataFrame):
                meta = meta.iloc[0]
            g2 = g.copy()
            g2["date"] = pd.to_datetime(g2["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            row = _soft_setup_row(code, g2, meta, mf_by_code.get(code), theme)
            if row:
                # 强制主主题为当前缺口主题（便于配额占坑）
                row["主题板块"] = theme
                themes = [theme] + [t for t in match_themes(row["行业"], row["名称"]) if t != theme]
                row["主题列表"] = ",".join(dict.fromkeys(themes))
                scored.append(row)
                sig_by_code[code] = detect_accumulation_breakout(
                    g2,
                    box_max_amp=0.45,
                    breakout_vol_ratio=1.05,
                    breakout_chg_min=0.005,
                    breakout_chg_max=0.15,
                    breakout_window_days=15,
                )
        scored.sort(key=lambda r: r["综合分"], reverse=True)
        need = int(theme_min.get(theme, 5))
        # 多取一些供总 Top 补齐
        take = scored[: max(need + 3, need)]
        for r in take:
            already.add(r["ts_code"])
            rows.append(r)
        print(f"  [theme_fill] {theme}: 池 {len(pool)} → 入选候选 {len(take)}")

    # 若总数仍不足，从所有主题池按软分再补
    if need_total > 0 and len(rows) < need_total:
        extra_need = need_total - len(rows)
        mask = theme_universe_mask(cand, list(REQUIRED_THEMES))
        pool = [c for c in cand.loc[mask, "ts_code"].tolist() if c not in already]
        extras: list[dict] = []
        for code in pool[:800]:  # 上限，避免过慢
            try:
                g = grp.get_group(code)
            except KeyError:
                continue
            if code not in basic_idx.index:
                continue
            meta = basic_idx.loc[code]
            if isinstance(meta, pd.DataFrame):
                meta = meta.iloc[0]
            themes = match_themes(meta.get("industry"), meta.get("name"))
            theme = themes[0] if themes else "其他"
            g2 = g.copy()
            g2["date"] = pd.to_datetime(g2["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            row = _soft_setup_row(code, g2, meta, mf_by_code.get(code), theme)
            if row:
                extras.append(row)
        extras.sort(key=lambda r: r["综合分"], reverse=True)
        for r in extras[:extra_need]:
            already.add(r["ts_code"])
            rows.append(r)
        print(f"  [theme_fill] 总数补齐 +{min(extra_need, len(extras))}")

    return rows


def _detect_on_codes(
    codes: set[str],
    daily_sorted: pd.DataFrame,
    sig_by_code: dict[str, dict],
    *,
    relaxed: bool = False,
    workers: int | None = None,
    progress_cb=None,
    cancel_check=None,
) -> list[str]:
    """对给定代码集合做信号检测（多进程），写回 sig_by_code，返回新命中列表。"""
    if not codes:
        return []

    # 已有 strict 命中的代码：strict 轮直接复用；relaxed 轮跳过
    pending: set[str] = set()
    hits: list[str] = []
    for code in codes:
        prev = sig_by_code.get(code)
        if prev and prev.get("is_breakout"):
            if not relaxed:
                hits.append(code)
            # relaxed 不覆盖已命中
            continue
        pending.add(code)

    if not pending:
        return hits

    kwargs: dict = {}
    if relaxed:
        kwargs = {
            "box_max_amp": RELAXED_BOX_MAX_AMP,
            "breakout_vol_ratio": RELAXED_BREAKOUT_VOL_RATIO,
            "breakout_chg_min": RELAXED_BREAKOUT_CHG_MIN,
            "breakout_chg_max": RELAXED_BREAKOUT_CHG_MAX,
            "breakout_window_days": RELAXED_BREAKOUT_WINDOW_DAYS,
            "box_max_mid_drawdown": RELAXED_BOX_MAX_MID_DRAWDOWN,  # 位置约束放宽但仍约束下跌中继
            "require_structure": False,  # B 池放宽结构，A 池仍要求完整箱体
        }

    label = "信号检测(relaxed)" if relaxed else "信号检测"
    found = detect_many(
        pending,
        daily_sorted,
        kwargs=kwargs,
        workers=workers,
        progress_cb=progress_cb,
        cancel_check=cancel_check,
        label=label,
    )
    for code, sig in found.items():
        if relaxed:
            if sig.get("is_breakout"):
                sig_by_code[code] = sig
                hits.append(code)
        else:
            sig_by_code[code] = sig
            if sig.get("is_breakout"):
                hits.append(code)
    return hits


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
) -> dict:
    """主扫描：A 池(strict 可交易) + B 池(观察，可选 theme_fill)。

    默认 top = A 池数量（15）。theme_fill 永不混入 A 池。
    cancel_check：返回 True 时在阶段间停止扫描（在不可中断的并行阶段内于分片粒度生效）。
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
        basic, trade_dates, daily, dbbasic, _ = load_market_data(days, force)
    except Exception as e:  # noqa: BLE001
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

    if daily is not None and not daily.empty and "trade_date" in daily.columns:
        max_d = str(pd.to_numeric(daily["trade_date"], errors="coerce").max()).split(".")[0]
        if max_d and max_d != "nan":
            max_d = max_d.zfill(8) if max_d.isdigit() else max_d
            trade_dates = [d for d in trade_dates if str(d) <= max_d] or trade_dates
    latest_date = trade_dates[-1] if trade_dates else ""
    if daily is not None and not daily.empty:
        latest_date = str(sorted(daily["trade_date"].astype(str).unique())[-1])
    if dbbasic is None or getattr(dbbasic, "empty", True) or "ts_code" not in getattr(dbbasic, "columns", []):
        try:
            dbbasic = data_fetch.get_daily_basic_by_dates([latest_date], sleep=0.2)
        except Exception:  # noqa: BLE001
            pass

    # 市场环境（优先 000300.SH）
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
        relax_pool = all_codes - already
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
        from sector_themes import annotate_themes
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
        from local_store import LocalStore, sync_fina_for_codes
        store = LocalStore()
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
                with store._connect() as conn:
                    conn.execute("DELETE FROM scan_result WHERE trade_date=?", (latest_date,))
            except Exception:  # noqa: BLE001
                pass
            store.upsert_scan_result(pd.DataFrame(scan_rows))
            try:
                sync_fina_for_codes([r["ts_code"] for r in scan_rows[:40]], verbose=False)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="横盘吸筹→启动 选股（A池可交易 / B池观察）")
    parser.add_argument("--top", type=int, default=TOP_N, help="A 池数量（默认15）")
    parser.add_argument("--days", type=int, default=HORIZON_DAYS, help="回看天数")
    parser.add_argument("--force", action="store_true", help="强制重新拉取数据")
    parser.add_argument("--max-check", type=int, default=None, help="限制检查数量（调试用）")
    parser.add_argument("--no-watch", action="store_true", help="不构建 B 观察池")
    parser.add_argument("--relaxed-in-a", action="store_true", help="允许 relaxed 进入 A 池")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="并行进程数（0=自动，1=单进程；默认读 config.SCAN_WORKERS）",
    )
    args = parser.parse_args()

    result = run_scan(
        top=args.top,
        days=args.days,
        force=args.force,
        max_check=args.max_check,
        build_watch=not args.no_watch,
        include_relaxed_in_a=args.relaxed_in_a,
        workers=args.workers,
    )

    df = result["df"]
    print(f"\n===== 扫描完成（{result['elapsed_sec']}s，workers={result.get('workers')}）=====")
    print(f"最新交易日: {result['latest_date']}  新鲜度: {result.get('freshness')}")
    print(f"环境: {result.get('regime')}")
    print(f"A池: {result.get('pool_report', {}).get('a_count')}  B池: {result.get('pool_report', {}).get('b_count')}")
    if df is not None and not df.empty:
        cols = [c for c in ["代码", "名称", "池", "筛选层级", "主题板块", "最新价", "综合分", "止损价", "目标1", "建议仓位%", "可交易"] if c in df.columns]
        print("\n-- A 池可交易 --")
        print(df[cols].to_string(index=False))
    dfb = result.get("df_b")
    if dfb is not None and not dfb.empty:
        cols = [c for c in ["代码", "名称", "池", "筛选层级", "主题板块", "综合分"] if c in dfb.columns]
        print("\n-- B 池观察(前10) --")
        print(dfb[cols].head(10).to_string(index=False))
    # 防守空 A 池也算成功完成
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
