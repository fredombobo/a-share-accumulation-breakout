"""
A/B 池选择与软主题偏好
======================
A 池：strict（+可选少量 relaxed）可交易
B 池：theme_fill / 观察，禁止与 A 混排
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.data.freshness import moneyflow_window_status
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


def filter_b_display(
    df: pd.DataFrame,
    *,
    min_score: float = 80.0,
    limit: int | None = None,
) -> pd.DataFrame:
    """限制 B 池展示分数；不改变完整资格快照。"""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    score_col = _score_col(df)
    if not score_col:
        return df.iloc[0:0].copy()
    scores = pd.to_numeric(df[score_col], errors="coerce")
    out = df.loc[scores.ge(float(min_score))].copy()
    if limit is not None:
        out = out.head(max(0, int(limit)))
    return out.reset_index(drop=True)


def fund_flow_quality_ok(
    mf_rows: pd.DataFrame | None,
    min_positive_days: int = 2,
    *,
    expected_dates: list[str] | None = None,
    expected_as_of: str = "",
    required_days: int = 5,
) -> tuple[bool, int]:
    """独立日期窗口完整，且净流入为正的天数 ≥ min_positive_days。

    无 net_mf_amount 时尝试用大单差额；仍无数据则 **不通过**（strict 宁缺毋滥）。
    """
    if mf_rows is None or getattr(mf_rows, "empty", True):
        return False, 0
    window = moneyflow_window_status(
        mf_rows, expected_dates=expected_dates, expected_as_of=expected_as_of,
        required_days=required_days,
    )
    if not window["complete"]:
        return False, 0
    df = mf_rows.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "", regex=False)
    df = df.loc[df["trade_date"].isin(window["expected_dates"])]
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


def qualified_pools(
    df: pd.DataFrame, *, can_publish_a: bool = True, include_relaxed_in_a: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Complete eligibility before any presentation limit; never changes the tier."""
    if df is None or df.empty:
        empty = pd.DataFrame(columns=["ts_code", "筛选层级", "池", "qualified_pool"])
        return empty.copy(), empty.copy()
    work = apply_soft_theme_bonus(df)
    tier_c = _tier_col(work)
    if not tier_c:
        tier_c = "筛选层级"
        work[tier_c] = "unknown"
    work[tier_c] = work[tier_c].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    sort_c = "排序分" if "排序分" in work else _score_col(work)
    sort_columns = [sort_c] if sort_c else []
    ascending = [False] if sort_c else []
    if "ts_code" in work:
        sort_columns.append("ts_code")
        ascending.append(True)
    if sort_columns:
        work = work.sort_values(sort_columns, ascending=ascending, kind="stable")
    if "ts_code" in work and work["ts_code"].duplicated().any():
        raise ValueError("duplicate candidate before qualification")
    eligible = work[tier_c].isin(["strict"] + (["relaxed"] if include_relaxed_in_a else [])) & bool(can_publish_a)
    work["qualified_pool"] = "B"
    work.loc[eligible, "qualified_pool"] = "A"
    a, b = work.loc[eligible].copy(), work.loc[~eligible].copy()
    if not b.empty:
        b["_strict_priority"] = b[tier_c].eq("strict").astype(int)
        b = b.sort_values("_strict_priority", ascending=False, kind="stable").drop(columns="_strict_priority")
    a["池"], b["池"] = "A", "B"
    return a.reset_index(drop=True), b.reset_index(drop=True)


def select_research_a(
    df: pd.DataFrame,
    *,
    target: int = 3,
    min_count: int = 1,
    max_count: int = 7,
    min_score: float = 70.0,
    min_box_days: int = 20,
    min_vol_ratio: float = 1.25,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """从观察候选中选出少量 ``A 研究候选``。

    这是盘后研究的兜底层，不改变生产 A 池的 strict/市场闸门语义。候选
    必须仍然来自 strict 或 relaxed 技术信号，且报价、基本面、资金窗口
    完整；theme_fill、data_incomplete 永远不会被提升。返回值中的第二个
    DataFrame 是移除研究候选后的剩余观察池，便于调用方保持 A/B 不重复。
    """
    empty = pd.DataFrame(columns=list(df.columns) if df is not None else [])
    report: dict[str, Any] = {
        "enabled": True,
        "target": max(0, int(target)),
        "min_count": max(0, int(min_count)),
        "max_count": max(0, int(max_count)),
        "min_score": float(min_score),
        "min_box_days": int(min_box_days),
        "min_vol_ratio": float(min_vol_ratio),
        "selected": 0,
        "selected_codes": [],
        "selected_tiers": {},
        "reason": "no_eligible_candidates",
    }
    if df is None or df.empty or max_count <= 0:
        return empty.copy(), (df.copy() if df is not None else empty.copy()), report

    work = apply_soft_theme_bonus(df).copy()
    tier_c = _tier_col(work) or "筛选层级"
    score_c = "排序分" if "排序分" in work else _score_col(work)
    if tier_c not in work.columns:
        return empty.copy(), df.copy(), report

    work[tier_c] = work[tier_c].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    # 研究候选必须保留完整的证据链。资金窗口缺失时宁可继续观察，
    # 不把数据问题包装成 A 候选。
    complete = pd.Series(True, index=work.index)
    if "data_missing_fields" in work.columns:
        complete &= work["data_missing_fields"].map(lambda value: not bool(value))
    if "fund_window" in work.columns:
        complete &= work["fund_window"].map(
            lambda value: isinstance(value, dict) and value.get("complete") is True
        )
    tiers = work[tier_c].isin(["strict", "relaxed"])
    scores = pd.to_numeric(work.get(score_c, pd.Series(index=work.index, dtype=float)), errors="coerce")
    box_days = pd.to_numeric(
        work.get("箱体天数", work.get("box_days", pd.Series(index=work.index))),
        errors="coerce",
    )
    vol_ratio = pd.to_numeric(
        work.get("量比", work.get("vol_ratio", pd.Series(index=work.index))),
        errors="coerce",
    )
    eligible = (
        tiers & complete & scores.ge(float(min_score)) &
        box_days.ge(int(min_box_days)) & vol_ratio.ge(float(min_vol_ratio))
    )
    candidates = work.loc[eligible].copy()

    # 当前阈值没有足够候选时，只下调相对评分 8 分；仍不放行缺数据或
    # theme_fill。这样可在一周内给出 1~7 只研究线索，同时保持质量下限。
    if len(candidates) < max(1, int(min_count)):
        relaxed_score = max(60.0, float(min_score) - 8.0)
        eligible_relaxed = tiers & complete & scores.ge(relaxed_score) & box_days.ge(int(min_box_days)) & vol_ratio.ge(float(min_vol_ratio))
        candidates = work.loc[eligible_relaxed].copy()
        if len(candidates):
            report["fallback_min_score"] = relaxed_score

    if candidates.empty:
        report["reason"] = "no_eligible_candidates"
        return empty.copy(), df.copy(), report

    if score_c and score_c in candidates.columns:
        candidates["_research_sort_score"] = pd.to_numeric(candidates[score_c], errors="coerce").fillna(0.0)
    else:
        candidates["_research_sort_score"] = 0.0
    candidates["_research_strict_priority"] = candidates[tier_c].eq("strict").astype(int)
    sort_columns = ["_research_strict_priority", "_research_sort_score"]
    ascending = [False, False]
    if "ts_code" in candidates.columns:
        sort_columns.append("ts_code")
        ascending.append(True)
    candidates = candidates.sort_values(sort_columns, ascending=ascending, kind="stable")
    wanted = min(max(1, int(target)), max(1, int(max_count)))
    selected = candidates.head(wanted).drop(columns=["_research_sort_score", "_research_strict_priority"], errors="ignore").copy()
    if selected.empty:
        return empty.copy(), df.copy(), report

    selected["池"] = "A"
    selected["qualified_pool"] = "A"
    selected["a_pool_mode"] = "RESEARCH_GATED"
    selected["研究候选"] = True
    selected["可交易"] = False
    reason_series = selected["入选理由"] if "入选理由" in selected.columns else pd.Series("", index=selected.index)
    selected["入选理由"] = reason_series.astype(str).map(
        lambda reason: "研究 A 候选（放宽条件/环境限额，不自动交易）；" + reason
    )

    selected_codes = set(selected["ts_code"].astype(str)) if "ts_code" in selected.columns else set()
    remaining = df.loc[~df["ts_code"].astype(str).isin(selected_codes)].copy() if "ts_code" in df.columns else df.copy()
    report.update({
        "selected": int(len(selected)),
        "selected_codes": sorted(selected_codes),
        "selected_tiers": selected[tier_c].value_counts().to_dict(),
        "reason": "research_quota_filled",
    })
    return selected.reset_index(drop=True), remaining.reset_index(drop=True), report


def split_pools(
    df: pd.DataFrame, *, top_a: int = 15, top_b: int = 30,
    include_relaxed_in_a: bool = False, regime_max_slots: int | None = None,
    can_publish_a: bool | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Project complete eligibility into Top display; preserve overflow as B observations."""
    top_a, top_b = max(0, int(top_a)), max(0, int(top_b))
    slots = top_a if regime_max_slots is None else min(top_a, max(0, int(regime_max_slots)))
    permitted = regime_max_slots != 0 if can_publish_a is None else can_publish_a
    full_a, full_b = qualified_pools(df, can_publish_a=permitted, include_relaxed_in_a=include_relaxed_in_a)
    tier_c = _tier_col(full_a) or _tier_col(full_b)
    strict_count = int(full_a[tier_c].eq("strict").sum() + full_b[tier_c].eq("strict").sum())
    a = full_a.head(slots).copy()
    withheld = full_a.iloc[slots:].copy()
    b = pd.concat([withheld, full_b], ignore_index=True)
    if not b.empty:
        b["_strict_priority"] = b[tier_c].eq("strict").astype(int)
        sort_c = "排序分" if "排序分" in b else _score_col(b)
        columns, ascending = ["_strict_priority"], [False]
        if sort_c:
            columns.append(sort_c); ascending.append(False)
        if "ts_code" in b:
            columns.append("ts_code"); ascending.append(True)
        b = b.sort_values(columns, ascending=ascending, kind="stable").drop(columns="_strict_priority")
        b.loc[b[tier_c].eq("strict"), "观察原因"] = "通过技术筛选但未获本次 A 池发布名额；仅作观察"
    b_available = len(b)
    a, b = a.reset_index(drop=True), b.head(top_b).reset_index(drop=True)
    a["池"], b["池"] = "A", "B"
    withheld_count = strict_count - int(a[tier_c].eq("strict").sum())
    displayed_strict = int(b[tier_c].eq("strict").sum())
    report = {
        "a_count": len(a), "b_count": len(b), "theme_soft": True,
        "a_tiers": a[tier_c].value_counts().to_dict(), "b_tiers": b[tier_c].value_counts().to_dict(),
        "qualified_strict": strict_count, "withheld_strict": withheld_count,
        "withheld_strict_displayed": displayed_strict,
        "withheld_strict_not_displayed": withheld_count - displayed_strict,
        "a_top_limit": top_a, "b_top_limit": top_b, "a_slots": slots,
        "a_eligible_count": len(full_a), "b_available_count": b_available,
        "total_candidates": len(full_a) + len(full_b),
        "qualified_counts": {"A": len(full_a), "B": len(full_b)},
    }
    return a, b, report
