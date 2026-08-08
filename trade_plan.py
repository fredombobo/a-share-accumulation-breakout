"""
交易卡片：入场 / 止损 / 目标 / 仓位 / 失效
========================================
个人轻量执行层，不接实盘。
"""
from __future__ import annotations

from typing import Any


def build_trade_card(
    *,
    price: float | None,
    box_high: float | None,
    box_low: float | None,
    breakout_date: str | None,
    tier: str = "strict",
    regime: str = "neutral",
    score: float | None = None,
) -> dict[str, Any]:
    """生成单票交易计划。"""
    px = float(price) if price is not None and price == price else None
    bh = float(box_high) if box_high is not None and box_high == box_high else None
    bl = float(box_low) if box_low is not None and box_low == box_low else None

    # 止损：箱体上沿下方 1% 与 -7% 取更紧（对多头）
    stop = None
    if px and bh:
        stop_box = bh * 0.99
        stop_pct = px * 0.93
        stop = max(stop_box, stop_pct) if stop_box < px else stop_pct
        # 若箱顶已低于现价很多，用 -7%
        if stop >= px:
            stop = px * 0.93
    elif px:
        stop = px * 0.93

    # 目标：+12% 或 1.5R
    target1 = None
    target2 = None
    if px and stop and stop < px:
        risk = px - stop
        target1 = px + risk * 1.5
        target2 = px + risk * 2.5
        # 同时设百分比上限
        target1 = min(target1, px * 1.12)
        target2 = min(target2, px * 1.20)

    # 仓位：环境 + 层级
    base = 0.15
    if regime == "attack":
        base = 0.18
    elif regime == "defense":
        base = 0.0
    if tier == "relaxed":
        base *= 0.6
    elif tier == "theme_fill":
        base = 0.0  # 观察池不建议开仓
    if score is not None and score < 55:
        base *= 0.7
    position_pct = round(min(0.20, max(0.0, base)) * 100, 1)

    # 失效：突破后超过 5 日未创新高则降级；最长持有 15 日
    return {
        "entry_ref": round(px, 2) if px else None,
        "entry_note": "突破确认日尾盘或次日开盘弱转强（回测二选一）",
        "stop_loss": round(stop, 2) if stop else None,
        "stop_rule": "收盘跌破箱体上沿*0.99 或 -7%（取触发先到者）",
        "target_1": round(target1, 2) if target1 else None,
        "target_2": round(target2, 2) if target2 else None,
        "target_rule": "目标1≈1.5R且≤+12%；目标2≈2.5R且≤+20%",
        "position_pct": position_pct,
        "max_hold_days": 15,
        "invalidate_days": 5,
        "box_high": round(bh, 2) if bh else None,
        "box_low": round(bl, 2) if bl else None,
        "breakout_date": breakout_date,
        "tradeable": tier == "strict" and position_pct > 0 and regime != "defense",
        "pool": "A" if tier == "strict" else ("A_relaxed" if tier == "relaxed" else "B"),
    }


def attach_trade_cards(df, regime: str = "neutral", sig_by_code: dict | None = None):
    """为结果 DataFrame 增加交易字段列。"""

    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    cards = []
    for _, r in out.iterrows():
        code = r.get("ts_code")
        sig = (sig_by_code or {}).get(code, {})
        tier = str(r.get("筛选层级") or r.get("tier") or "strict")
        price = r.get("最新价") if "最新价" in r else r.get("price")
        card = build_trade_card(
            price=price,
            box_high=sig.get("box_high") if sig else r.get("box_high"),
            box_low=sig.get("box_low") if sig else r.get("box_low"),
            breakout_date=r.get("突破日") or r.get("breakout_date"),
            tier=tier,
            regime=regime,
            score=r.get("综合分") if "综合分" in r else r.get("total_score"),
        )
        cards.append(card)
    out["止损价"] = [c["stop_loss"] for c in cards]
    out["目标1"] = [c["target_1"] for c in cards]
    out["目标2"] = [c["target_2"] for c in cards]
    out["建议仓位%"] = [c["position_pct"] for c in cards]
    out["可交易"] = [c["tradeable"] for c in cards]
    # 保留 split_pools 写入的 A/B；仅在缺失时回填
    if "池" not in out.columns:
        out["池"] = [c["pool"] for c in cards]
    else:
        out["池"] = [
            (str(existing) if str(existing) in ("A", "B") else c["pool"])
            for existing, c in zip(out["池"].tolist(), cards)
        ]
    out["最长持有日"] = [c["max_hold_days"] for c in cards]
    out["_trade_card"] = cards
    return out
