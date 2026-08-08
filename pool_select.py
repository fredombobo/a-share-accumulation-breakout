"""
A/B 池选择与软主题偏好
======================
A 池：strict（+可选少量 relaxed）可交易
B 池：theme_fill / 观察，禁止与 A 混排
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from sector_themes import _dedup_themes_map

PREFERRED_THEMES = ("AI应用", "半导体", "光模块", "机器人", "电力", "芯片")
THEME_SOFT_BONUS = 2.0  # 主题软加分压低，避免盖过「长横盘+明确信号」


def _tier_col(df: pd.DataFrame) -> str:
    if "筛选层级" in df.columns:
        return "筛选层级"
    if "tier" in df.columns:
        return "tier"
    return ""


def _score_col(df: pd.DataFrame) -> str:
    if "综合分" in df.columns:
        return "综合分"
    if "total_score" in df.columns:
        return "total_score"
    return ""


def apply_soft_theme_bonus(df: pd.DataFrame) -> pd.DataFrame:
    """偏好主题软加分，不硬凑数量。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    sc = _score_col(out)
    if not sc:
        return out
    ind = out.get("行业")
    if ind is None:
        ind = out.get("industry")
    nm = out.get("名称")
    if nm is None:
        nm = out.get("name")
    if ind is None or nm is None:
        # 无主题列可判，全部不加分
        out["主题软加分"] = 0.0
        out["排序分"] = pd.to_numeric(out[sc], errors="coerce").fillna(0) + out["主题软加分"]
        return out
    lookup = _dedup_themes_map(ind, nm)
    keys_ind = ind.map(lambda v: str(v or "").strip())
    keys_nm = nm.map(lambda v: str(v or "").strip())
    bonuses = [
        THEME_SOFT_BONUS if any(t in PREFERRED_THEMES for t in lookup[(i, n)]) else 0.0
        for i, n in zip(keys_ind, keys_nm)
    ]
    out["主题软加分"] = bonuses
    out["排序分"] = pd.to_numeric(out[sc], errors="coerce").fillna(0) + out["主题软加分"]
    return out


def fund_flow_quality_ok(mf_rows: pd.DataFrame | None, min_positive_days: int = 2) -> tuple[bool, int]:
    """近窗内净流入为正的天数 ≥ min_positive_days。

    无 net_mf_amount 时尝试用大单差额；仍无数据则 **不通过**（strict 宁缺毋滥）。
    """
    if mf_rows is None or getattr(mf_rows, "empty", True):
        return False, 0
    df = mf_rows
    if "net_mf_amount" in df.columns:
        net = pd.to_numeric(df["net_mf_amount"], errors="coerce").fillna(0.0)
    elif all(c in df.columns for c in ("buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount")):
        buy = pd.to_numeric(df["buy_elg_amount"], errors="coerce").fillna(0) + pd.to_numeric(df["buy_lg_amount"], errors="coerce").fillna(0)
        sell = pd.to_numeric(df["sell_elg_amount"], errors="coerce").fillna(0) + pd.to_numeric(df["sell_lg_amount"], errors="coerce").fillna(0)
        net = buy - sell
    else:
        return False, 0
    pos_days = int((net > 0).sum())
    return pos_days >= min_positive_days, pos_days


def breakout_freshness_bonus(
    breakout_date: str | None,
    latest_date: str,
    max_lag: int = 5,
    trade_dates: list[str] | None = None,
) -> float:
    """突破越新越好（优先用交易日序列算 lag，避免周末放大）。"""
    if not breakout_date or not latest_date:
        return 0.0
    bd = "".join(ch for ch in str(breakout_date) if ch.isdigit())[:8]
    ld = "".join(ch for ch in str(latest_date) if ch.isdigit())[:8]
    lag: int | None = None
    if trade_dates:
        td = [str(x) for x in trade_dates]
        if bd in td and ld in td:
            lag = td.index(ld) - td.index(bd)
        elif ld in td:
            # 突破日不在列表：用最近不超过 ld 的交易日
            prior = [d for d in td if d <= bd]
            if prior:
                lag = td.index(ld) - td.index(prior[-1])
    if lag is None:
        try:
            from datetime import datetime
            lag = (datetime.strptime(ld, "%Y%m%d") - datetime.strptime(bd, "%Y%m%d")).days
            # 日历日粗略折算交易日
            lag = max(0, lag * 5 // 7)
        except ValueError:
            return 0.0
    if lag <= 0:
        return 8.0
    if lag == 1:
        return 5.0
    if lag == 2:
        return 3.0
    if lag <= max_lag:
        return 0.0
    return -5.0


def split_pools(
    df: pd.DataFrame,
    *,
    top_a: int = 15,
    top_b: int = 30,
    include_relaxed_in_a: bool = False,
    regime_max_slots: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """拆分 A/B 池。

    A: strict（可选 relaxed）
    B: theme_fill 及其它观察票
    """
    empty = pd.DataFrame()
    report: dict[str, Any] = {
        "a_count": 0,
        "b_count": 0,
        "a_tiers": {},
        "b_tiers": {},
        "theme_soft": True,
    }
    if df is None or df.empty:
        return empty, empty, report

    work = apply_soft_theme_bonus(df)
    tier_c = _tier_col(work)
    if not tier_c:
        work["筛选层级"] = "strict"
        tier_c = "筛选层级"

    a_mask = work[tier_c].astype(str).isin(["strict"] + (["relaxed"] if include_relaxed_in_a else []))
    b_mask = ~a_mask

    a = work.loc[a_mask].copy()
    b = work.loc[b_mask].copy()

    sort_c = "排序分" if "排序分" in a.columns else _score_col(a)
    if sort_c and not a.empty:
        a = a.sort_values(sort_c, ascending=False)
    if sort_c and not b.empty and sort_c in b.columns:
        b = b.sort_values(sort_c, ascending=False)

    slots = top_a if regime_max_slots is None else min(top_a, max(0, regime_max_slots))
    a = a.head(slots).reset_index(drop=True)
    b = b.head(top_b).reset_index(drop=True)

    a["池"] = "A"
    b["池"] = "B"
    report["a_count"] = len(a)
    report["b_count"] = len(b)
    if tier_c in work.columns:
        report["a_tiers"] = a[tier_c].value_counts().to_dict() if not a.empty else {}
        report["b_tiers"] = b[tier_c].value_counts().to_dict() if not b.empty else {}
    return a, b, report
