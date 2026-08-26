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

import pandas as pd

from config import (
    FUND_SCORE_WEIGHT,
    FUNDAMENTAL_SCORE_WEIGHT,
    MAX_MV_YI,
    MAX_PB,
    MAX_PE,
    MIN_LIST_DAYS,
    MIN_MV_YI,
    MIN_PB,
    MIN_PE,
    MIN_PRICE,
    SCORE_MV_BEST_HIGH,
    SCORE_MV_BEST_LOW,
    SCORE_MV_OK_HIGH,
    SCORE_MV_OK_LOW,
    SCORE_PB_BEST_HIGH,
    SCORE_PB_BEST_LOW,
    SCORE_PB_OK_HIGH,
    SCORE_PE_BEST_HIGH,
    SCORE_PE_BEST_LOW,
    SCORE_TR_BEST_HIGH,
    SCORE_TR_BEST_LOW,
    SCORE_TR_OK_HIGH,
    SIGNAL_SCORE_WEIGHT,
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


def _tail_trading_days(mf_rows: pd.DataFrame, days: int) -> pd.DataFrame:
    """截取最近 days 个交易日（按 trade_date 排序去重后取尾部）。

    修复：原实现把全部历史累计当作「近5日」；这里必须按交易日截断，
    避免详情页「近5日资金流」实际累计了整段历史。
    """
    if mf_rows is None or mf_rows.empty:
        return mf_rows if mf_rows is not None else pd.DataFrame()
    if "trade_date" not in mf_rows.columns:
        return mf_rows.tail(days) if days and days > 0 else mf_rows
    df = mf_rows.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    last_dates = df["trade_date"].sort_values().drop_duplicates().tail(days)
    return df[df["trade_date"].isin(set(last_dates))]


def calc_fund_flow_strength(mf_rows: pd.DataFrame, days: int | None = None) -> tuple[float, float, float]:
    """返回 (主力净流入万元, 资金流强度分 0-100, 净流入/成交额比)

    days：仅统计最近 N 个交易日（默认 None=全部）。详情页「近5日」应传 days=5。
    """
    if mf_rows is None or mf_rows.empty:
        return 0.0, 0.0, 0.0
    if days and days > 0:
        mf_rows = _tail_trading_days(mf_rows, days)
        if mf_rows is None or mf_rows.empty:
            return 0.0, 0.0, 0.0
    net = calc_main_net_inflow(mf_rows)
    # 同期成交额估算：优先 amount 列（万元）；缺失时回退为「完整主力成交额」
    # （买/卖超大单+大单合计 ≈ 主力双边成交额），避免仅用买入侧造成分母减半、比率翻倍。
    if "amount" in mf_rows.columns:
        amt = pd.to_numeric(mf_rows["amount"], errors="coerce").fillna(0.0).sum()
    else:
        cols = [c for c in MAIN_FLOW_COLS if c in mf_rows.columns]
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

    # 估值：PE 越接近合理区间(SCORE_PE_BEST_LOW~HIGH)分越高
    if pd.notna(pe) and pe > 0:
        if SCORE_PE_BEST_LOW <= pe <= SCORE_PE_BEST_HIGH:
            s = 30.0
        elif 0 < pe < SCORE_PE_BEST_LOW:
            s = 20.0
        elif SCORE_PE_BEST_HIGH < pe <= MAX_PE:
            s = 15.0
        else:
            s = 5.0
        score += s
        notes.append(f"PE={pe:.1f}({s:.0f}分)")
    else:
        notes.append("PE无数据")

    # 市净率：越低越好，SCORE_PB_BEST_LOW~HIGH 为佳
    if pd.notna(pb) and pb > 0:
        if SCORE_PB_BEST_LOW <= pb <= SCORE_PB_BEST_HIGH:
            s = 20.0
        elif SCORE_PB_BEST_HIGH < pb <= SCORE_PB_OK_HIGH:
            s = 12.0
        elif 0 < pb < SCORE_PB_BEST_LOW:
            s = 15.0
        else:
            s = 5.0
        score += s
        notes.append(f"PB={pb:.2f}({s:.0f}分)")
    else:
        notes.append("PB无数据")

    # 市值：SCORE_MV_BEST_LOW~HIGH 亿 适中（有弹性且不太小）
    if pd.notna(mv) and mv > 0:
        if SCORE_MV_BEST_LOW <= mv <= SCORE_MV_BEST_HIGH:
            s = 25.0
        elif SCORE_MV_BEST_HIGH < mv <= SCORE_MV_OK_HIGH or SCORE_MV_OK_LOW <= mv < SCORE_MV_BEST_LOW:
            s = 18.0
        else:
            s = 8.0
        score += s
        notes.append(f"市值{mv:.0f}亿({s:.0f}分)")
    else:
        notes.append("市值无数据")

    # 换手率：SCORE_TR_BEST_LOW~HIGH 活跃但不过热
    if pd.notna(tr) and tr > 0:
        if SCORE_TR_BEST_LOW <= tr <= SCORE_TR_BEST_HIGH:
            s = 25.0
        elif SCORE_TR_BEST_HIGH < tr <= SCORE_TR_OK_HIGH:
            s = 15.0
        elif 0 < tr < SCORE_TR_BEST_LOW:
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
    param_weight: float = 1.0,
) -> tuple[float, dict]:
    """综合打分：信号 + 资金流 + 基本面，权重见 config.SIGNAL/FUND/FUNDAMENTAL_SCORE_WEIGHT。

    信号权重略提高，使「长横盘 + 明确突破」在排序中更占主导。
    param_weight：策略实验室回灌的历史验证权重（active 参数样本外 PF），默认 1.0 不生效。
    """
    sig_score = score_breakout_strength(sig)
    fund_score = fund_flow_score
    fund_notes: list[str] = []
    basic_score, basic_notes = score_fundamentals(fund_row)

    base = (
        sig_score * SIGNAL_SCORE_WEIGHT
        + fund_score * FUND_SCORE_WEIGHT
        + basic_score * FUNDAMENTAL_SCORE_WEIGHT
    )
    # 回灌权重：历史验证好的策略参数给信号更高的排序分；防 0/负值破坏排序
    weight = max(float(param_weight or 1.0), 0.1)
    total = round(base * weight, 1)

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
        "策略验证权重": round(weight, 3),
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
            age_days = (_dt.datetime.now(_dt.UTC).date() - ld).days
            if age_days < MIN_LIST_DAYS:
                fails.append("次新股")
        except ValueError:
            pass
    return (len(fails) == 0), fails
