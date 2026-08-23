"""
扫描内核 —— 单标的结果评估
===========================
职责：只计算「单只股票/单组候选」的结果行：
  - apply_box_ladder   横盘时长阶梯
  - _score_codes       strict/relaxed 候选打分（基本面+资金流+综合分）
  - _soft_setup_row    主题补齐软分
  - _theme_soft_fill   缺口主题观察池软评分
  - observed_signal    主题观察的信号默认值
  - _detect_on_codes   多进程信号检测并写回 sig_by_code

不负责：数据读取、候选集合、进程/取消/进度/排序/聚合（见 data_loader/prefilter/orchestrator）。
ENTRY、评分公式、阈值、默认参数与历史 run_screener 完全一致。
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pandas as pd

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import config as _cfg
import data_fetch
from parallel_scan import detect_many
from pool_select import (
    breakout_freshness_bonus,
    fund_flow_quality_ok,
)
from scoring import (
    calc_fund_flow_strength,
    fund_positive_days,
    fundamental_filter_passes,
    is_delisted_name,
    is_st_name,
)
from sector_themes import match_themes, theme_universe_mask
from signals import detect_accumulation_breakout

# 兼容旧进程缓存的 config（热更新前无此字段）
BOX_LADDER_DAYS = tuple(getattr(_cfg, "BOX_LADDER_DAYS", (125, 105, 84, 63, 42, 20)))
TARGET_SELECT_COUNT = int(getattr(_cfg, "TARGET_SELECT_COUNT", 20) or 20)

from config import (
    FUND_POSITIVE_DAYS_MIN,
    RELAXED_BOX_MAX_AMP,
    RELAXED_BOX_MAX_MID_DRAWDOWN,
    RELAXED_BREAKOUT_CHG_MAX,
    RELAXED_BREAKOUT_CHG_MIN,
    RELAXED_BREAKOUT_VOL_RATIO,
    RELAXED_BREAKOUT_WINDOW_DAYS,
)


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
    *,
    signal: dict | None = None,
) -> dict | None:
    """主题强制补齐：不要求完整突破，按箱体质量+贴近上沿+资金流打软分。"""
    from scoring import score_fundamentals

    sig = signal
    if sig is None:
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


def observed_signal(code: str, sig_by_code: dict[str, dict]) -> dict:
    """主题观察：已检测信号直接复用；否则返回无突破的观察信号（不重复检测）。"""
    return sig_by_code.get(code) or {
        "is_breakout": False,
        "reasons": ["未通过启动预筛，仅作主题观察"],
        "box_days": 0,
        "box_amp": None,
        "box_high": None,
        "latest_close": None,
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
    """对缺口主题做观察池软评分；不得为补配额再次运行交易信号。"""
    from config import REQUIRED_THEMES

    rows: list[dict] = []
    grp = daily_sorted.groupby("ts_code")
    basic_idx = basic_latest.set_index("ts_code", drop=False)

    # 确保资金流覆盖主题池（批量已在库中）
    if not mf_by_code:
        mf = data_fetch.get_moneyflow_by_dates(mf_dates, sleep=0.15)
        if not mf.empty:
            mf_by_code.update({c: g for c, g in mf.groupby("ts_code")})

    theme_pools = {
        theme: [
            c for c in cand.loc[theme_universe_mask(cand, [theme]), "ts_code"].tolist()
            if c not in already
        ]
        for theme in shortfall_themes
    }
    all_theme_pool = [
        c for c in cand.loc[theme_universe_mask(cand, list(REQUIRED_THEMES)), "ts_code"].tolist()
        if c not in already
    ]

    for theme in shortfall_themes:
        pool = theme_pools[theme]
        scored: list[dict] = []
        for code in pool:
            if code not in grp.groups:
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
            signal = observed_signal(code, sig_by_code)
            row = _soft_setup_row(
                code, g2, meta, mf_by_code.get(code), theme, signal=signal
            )
            if row:
                # 强制主主题为当前缺口主题（便于配额占坑）
                row["主题板块"] = theme
                themes = [theme] + [t for t in match_themes(row["行业"], row["名称"]) if t != theme]
                row["主题列表"] = ",".join(dict.fromkeys(themes))
                scored.append(row)
                sig_by_code.setdefault(code, signal)
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
        pool = [c for c in all_theme_pool if c not in already]
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
            row = _soft_setup_row(
                code, g2, meta, mf_by_code.get(code), theme, signal=observed_signal(code, sig_by_code)
            )
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

    kwargs: dict[str, Any] = {}
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
