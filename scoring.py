"""
基本面过滤与综合打分
====================
阶段3 资金流确认：
  - 近 FUND_FLOW_DAYS 日主力净流入（net_mf_amount 或大单+超大单净额）累计为正
  - 资金流强度 = 净流入 / 同期成交额

阶段4 基本面过滤：
  - PE(TTM) ∈ [MIN_PE, MAX_PE]
  - PB ∈ [MIN_PB, MAX_PB]
  - 总市值 ∈ [MIN_MV_YI, MAX_MV_YI]
  - 非 ST、非退市、上市满 MIN_LIST_DAYS
  - 价格 ≥ MIN_PRICE

综合打分（0-100）：
  - 信号强度分（横盘时长+突破明确度）：55%
  - 资金流分：25%
  - 基本面分：20%
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    FUND_FLOW_DAYS,
    FUND_FLOW_MIN_RATIO,
    MAX_MV_YI,
    MAX_PB,
    MAX_PE,
    MIN_LIST_DAYS,
    MIN_MV_YI,
    MIN_PB,
    MIN_PE,
    MIN_PRICE,
)
from signals import score_breakout_strength

# 主力 = 超大单 + 大单
MAIN_FLOW_COLS = ["buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"]


def is_st_name(name: str) -> bool:
    n = str(name).upper()
    return "ST" in n or "*ST" in n


def is_delisted_name(name: str) -> bool:
    return "退" in str(name)


def calc_main_net_inflow(mf_rows: pd.DataFrame) -> float:
    """计算近 N 日主力净流入（万元）。net_mf_amount 若缺失则用买卖大单差额。"""
    if mf_rows is None or mf_rows.empty:
        return 0.0
    if "net_mf_amount" in mf_rows.columns:
        net = pd.to_numeric(mf_rows["net_mf_amount"], errors="coerce").fillna(0.0)
        return float(net.sum())
    # 回退：大单+超大单买入 - 卖出
    buy = mf_rows["buy_elg_amount"].astype(float) + mf_rows["buy_lg_amount"].astype(float)
    sell = mf_rows["sell_elg_amount"].astype(float) + mf_rows["sell_lg_amount"].astype(float)
    return float((buy - sell).sum())


def calc_fund_flow_strength(mf_rows: pd.DataFrame) -> tuple[float, float, float]:
    """返回 (主力净流入万元, 资金流强度分 0-100, 净流入/成交额比)"""
    if mf_rows is None or mf_rows.empty:
        return 0.0, 0.0, 0.0
    net = calc_main_net_inflow(mf_rows)
    # 同期成交额估算：使用 amount 列（万元）或买卖总量
    if "amount" in mf_rows.columns:
        amt = pd.to_numeric(mf_rows["amount"], errors="coerce").fillna(0.0).sum()
    else:
        cols = [c for c in ("buy_elg_amount", "buy_lg_amount", "buy_md_amount", "buy_sm_amount") if c in mf_rows.columns]
        amt = mf_rows[cols].astype(float).sum().sum() if cols else 0.0
    ratio = (net / amt) if amt > 0 else 0.0
    # 强度分：净流入占比 0.5% → 60分，2% → 90分，负 → 0
    if net <= 0:
        score = 0.0
    else:
        score = min(100.0, 60 + ratio * 1500.0)
    # 质量：多日同向净流入加分
    if "net_mf_amount" in mf_rows.columns:
        daily = pd.to_numeric(mf_rows["net_mf_amount"], errors="coerce").fillna(0.0)
        pos_days = int((daily > 0).sum())
        if pos_days >= 3:
            score = min(100.0, score + 10.0)
        elif pos_days >= 2:
            score = min(100.0, score + 5.0)
        elif pos_days <= 1 and net > 0:
            score = max(0.0, score - 15.0)  # 单日暴量骗炮降权
    return net, round(score, 1), ratio


def fund_positive_days(mf_rows: pd.DataFrame | None) -> int:
    if mf_rows is None or getattr(mf_rows, "empty", True) or "net_mf_amount" not in mf_rows.columns:
        return 0
    daily = pd.to_numeric(mf_rows["net_mf_amount"], errors="coerce").fillna(0.0)
    return int((daily > 0).sum())


def score_fundamentals(row: pd.Series) -> tuple[float, list[str]]:
    """基本面分（0-100）与打分明细。row 需含 pe, pb, total_mv_yi, turnover_rate。"""
    score = 0.0
    notes: list[str] = []
    pe = row.get("pe")
    pb = row.get("pb")
    mv = row.get("total_mv_yi")
    tr = row.get("turnover_rate")

    # 估值：PE 越接近合理区间(8-30)分越高
    if pd.notna(pe) and pe > 0:
        if 8 <= pe <= 30:
            s = 30.0
        elif 0 < pe < 8:
            s = 20.0
        elif 30 < pe <= MAX_PE:
            s = 15.0
        else:
            s = 5.0
        score += s
        notes.append(f"PE={pe:.1f}({s:.0f}分)")
    else:
        notes.append("PE无数据")

    # 市净率：越低越好，0.8-4 为佳
    if pd.notna(pb) and pb > 0:
        if 0.8 <= pb <= 4:
            s = 20.0
        elif 4 < pb <= 8:
            s = 12.0
        elif 0 < pb < 0.8:
            s = 15.0
        else:
            s = 5.0
        score += s
        notes.append(f"PB={pb:.2f}({s:.0f}分)")
    else:
        notes.append("PB无数据")

    # 市值：50-500亿 适中（有弹性且不太小）
    if pd.notna(mv) and mv > 0:
        if 50 <= mv <= 500:
            s = 25.0
        elif 500 < mv <= 1500:
            s = 18.0
        elif 30 <= mv < 50:
            s = 18.0
        else:
            s = 8.0
        score += s
        notes.append(f"市值{mv:.0f}亿({s:.0f}分)")
    else:
        notes.append("市值无数据")

    # 换手率：2%-12% 活跃但不过热
    if pd.notna(tr) and tr > 0:
        if 2 <= tr <= 12:
            s = 25.0
        elif 12 < tr <= 20:
            s = 15.0
        elif 0 < tr < 2:
            s = 12.0
        else:
            s = 5.0
        score += s
        notes.append(f"换手{tr:.1f}%({s:.0f}分)")
    else:
        notes.append("换手无数据")

    return round(score, 1), notes


def build_master_score(
    sig: dict,
    fund_flow_score: float,
    fund_net: float,
    fund_ratio: float,
    fund_row: pd.Series,
) -> tuple[float, dict]:
    """综合打分：信号 55% + 资金流 25% + 基本面 20%。

    信号权重略提高，使「长横盘 + 明确突破」在排序中更占主导。
    """
    sig_score = score_breakout_strength(sig)
    fund_score, fund_notes = fund_flow_score, []
    basic_score, basic_notes = score_fundamentals(fund_row)

    total = round(sig_score * 0.55 + fund_score * 0.25 + basic_score * 0.20, 1)

    detail = {
        "信号强度分": sig_score,
        "资金流分": fund_score,
        "基本面分": basic_score,
        "主力净流入(万)": round(fund_net, 0),
        "净流入/成交额": round(fund_ratio * 100, 2),
        "资金流说明": fund_notes,
        "基本面说明": basic_notes,
        "箱体天数": sig.get("box_days"),
        "量比": sig.get("breakout_vol_ratio"),
        "箱体振幅": sig.get("box_amp"),
    }
    return total, detail


def fundamental_filter_passes(row: pd.Series) -> tuple[bool, list[str]]:
    """硬性过滤条件。返回 (是否通过, 未通过原因列表)"""
    fails: list[str] = []
    name = str(row.get("name", ""))
    if is_st_name(name):
        fails.append("ST")
    if is_delisted_name(name):
        fails.append("退市")
    if pd.notna(row.get("pe")) and not (MIN_PE <= row["pe"] <= MAX_PE):
        fails.append(f"PE={row['pe']:.1f}超限")
    if pd.notna(row.get("pb")) and not (MIN_PB <= row["pb"] <= MAX_PB):
        fails.append(f"PB={row['pb']:.2f}超限")
    mv = row.get("total_mv_yi")
    if pd.notna(mv) and not (MIN_MV_YI <= mv <= MAX_MV_YI):
        fails.append(f"市值{mv:.0f}亿超限")
    if pd.notna(row.get("close")) and row["close"] < MIN_PRICE:
        fails.append("股价过低")
    list_date = str(row.get("list_date", ""))
    if list_date and list_date != "nan":
        try:
            import datetime as _dt
            ld = _dt.datetime.strptime(list_date, "%Y%m%d").date()
            age_days = (_dt.date.today() - ld).days
            if age_days < MIN_LIST_DAYS:
                fails.append("次新股")
        except ValueError:
            pass
    return (len(fails) == 0), fails
