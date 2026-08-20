"""可信证据报告：与 A 池入场定义对齐的成本后 IS/OOS 摘要。

不跑全量 Lab 网格（那是 /lab 的职责）；本模块用**默认 fixed 出场**
在自动研究窗上抽样回测，回答：

1. 当前数据 mode 是否 full
2. 形态基线在 IS / OOS 上毛收益与净成本表现
3. 是否触达 can_promote 最低门槛（默认不 beat baseline → 不晋级）
4. 写入 fingerprint 与 entry_definition_id 便于复现
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.entry_definition import (
    ENTRY_DEFINITION_ID,
    breakout_in_recent_window,
    definition_snapshot,
    resolve_entry_from_signal,
)
from ab_screener.domain.entry_registry import report_entry_fingerprint, verify_report_entry_fingerprint
from ab_screener.domain.research_gate import can_promote_profile
from ab_screener.research.cost_adjustment import cost_adjusted_trade, summarize_costed_trades
from ab_screener.research.trusted_run import COST_ASSUMPTIONS, COST_VERSION
from config import HORIZON_DAYS, MAX_HOLD_DAYS, OUT_DIR, STOP_LOSS_PCT, TARGET_PCT_1
from research_windows import recommend_research_plan
from signals import detect_accumulation_breakout
from trade_sim import simulate_trade, summarize

_TZ = ZoneInfo("Asia/Shanghai")


def _prepare_bars(df: Any) -> Any:
    import pandas as pd

    out = df.sort_values("trade_date").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close", "pre_close", "vol"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        elif col == "vol" and "volume" in out.columns:
            out["vol"] = pd.to_numeric(out["volume"], errors="coerce")
    out["trade_date"] = out["trade_date"].astype(str)
    # signals.detect_accumulation_breakout 读 row["date"]
    out["date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def _collect_trades(
    *,
    store: Any,
    start: str,
    end: str,
    step: int,
    max_codes: int,
    horizon: int,
    stop_pct: float,
    target_pct: float,
    max_hold: int,
) -> list[dict[str, Any]]:
    """形态信号 → ENTRY v1 入场 → fixed 出场 → 成本调整。"""
    basic = store.load_stock_basic()
    if basic is None or basic.empty:
        return []
    codes = [
        str(c) for c in basic["ts_code"].astype(str).tolist()
        if str(c).endswith((".SH", ".SZ"))
    ][:max_codes]

    cal = [str(d) for d in (store.distinct_dates("daily") or [])]
    cal = [d for d in cal if start <= d <= end]
    sample_days = cal[:: max(1, step)]
    cal_index = {d: i for i, d in enumerate(cal)}
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for code in codes:
        df = store.load_daily(ts_codes=[code], start=start, end=end)
        if df is None or len(df) < 60:
            continue
        bars = _prepare_bars(df)
        dts = bars["trade_date"].astype(str).tolist()
        dts_set = set(dts)

        for day in sample_days:
            day_i = cal_index.get(day, -1)
            if day_i < 60:
                continue
            win_start = cal[max(0, day_i - horizon)]
            win = bars[(bars["trade_date"] >= win_start) & (bars["trade_date"] <= day)].reset_index(drop=True)
            if len(win) < 60:
                continue
            sig = detect_accumulation_breakout(win)
            if not sig.get("is_breakout"):
                continue
            if not breakout_in_recent_window(sig.get("breakout_date"), day, cal):
                continue
            resolved = resolve_entry_from_signal(bars, sig)
            if not resolved["ok"]:
                continue
            bd = resolved["breakout_date"]
            if bd not in dts_set:
                continue
            key = (code, bd)
            if key in seen:
                continue
            seen.add(key)
            sig_i = int(resolved["signal_index"])
            sim = simulate_trade(
                bars,
                sig_i,
                mode="fixed",
                params={"stop_pct": stop_pct, "target_pct": target_pct, "max_hold": max_hold},
            )
            if not sim.get("ok"):
                continue
            cost = cost_adjusted_trade(bars, sim)
            trades.append({
                "date": day,
                "ts_code": code,
                "breakout_date": bd,
                "ret": sim["ret"],
                "days": sim["days"],
                "exit": sim["exit"],
                "win": sim["win"],
                "max_dd": sim.get("max_dd"),
                "cost": cost,
                "entry_definition_id": ENTRY_DEFINITION_ID,
            })
    return trades


def _window_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gross = summarize(trades)
    costed = summarize_costed_trades(trades)
    return {
        "gross": gross,
        "net": costed,
        "n_trades": gross.get("n_trades", 0),
        "win_rate": gross.get("win_rate"),
        "profit_factor": gross.get("profit_factor"),
        "max_drawdown": gross.get("max_drawdown"),
        "net_n_trades": costed.get("net_n_trades"),
        "net_win_rate": costed.get("net_win_rate"),
        "net_profit_factor": costed.get("net_profit_factor"),
        "net_max_drawdown": costed.get("net_max_drawdown"),
        "net_avg_return": costed.get("net_avg_return"),
    }


def build_evidence_report(
    *,
    store: Any | None = None,
    step: int = 10,
    max_codes: int = 250,
    horizon: int = HORIZON_DAYS,
    stop_pct: float = STOP_LOSS_PCT,
    target_pct: float = TARGET_PCT_1,
    max_hold: int = MAX_HOLD_DAYS,
    beats_baseline: bool | None = None,
) -> dict[str, Any]:
    """生成证据包（字典）。beats_baseline 默认 None → 门禁记为未验证。"""
    from local_store import LocalStore

    store = store or LocalStore()
    plan = recommend_research_plan()
    plan_d = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)

    is_start, is_end = plan_d["is_start"], plan_d["is_end"]
    oos_start, oos_end = plan_d["oos_start"], plan_d["oos_end"]
    mode = plan_d["mode"]

    print(f"[evidence] mode={mode} IS={is_start}~{is_end} OOS={oos_start}~{oos_end}")
    print(f"[evidence] max_codes={max_codes} step={step}")

    is_trades = _collect_trades(
        store=store, start=is_start, end=is_end, step=step, max_codes=max_codes,
        horizon=horizon, stop_pct=stop_pct, target_pct=target_pct, max_hold=max_hold,
    )
    print(f"[evidence] IS trades={len(is_trades)}")
    oos_trades = _collect_trades(
        store=store, start=oos_start, end=oos_end, step=step, max_codes=max_codes,
        horizon=horizon, stop_pct=stop_pct, target_pct=target_pct, max_hold=max_hold,
    )
    print(f"[evidence] OOS trades={len(oos_trades)}")

    is_m = _window_metrics(is_trades)
    oos_m = _window_metrics(oos_trades)

    # 基线对比默认未跑 → False（fail-closed）；调用方可显式传入
    if beats_baseline is None:
        beats_flag = False
        baseline_note = "未跑双基线；门禁 beats_baseline=False（fail-closed）"
    else:
        beats_flag = bool(beats_baseline)
        baseline_note = f"调用方指定 beats_baseline={beats_flag}"

    promotion = can_promote_profile(
        research_mode=mode,
        oos_net_pf=oos_m.get("net_profit_factor"),
        oos_max_dd=oos_m.get("net_max_drawdown"),
        oos_win_rate=oos_m.get("net_win_rate"),
        beats_baseline=beats_flag,
    )

    report = {
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "entry_definition_id": ENTRY_DEFINITION_ID,
        "entry_semantic_hash": report_entry_fingerprint(ENTRY_DEFINITION_ID)["entry_semantic_hash"],
        "entry_definition": definition_snapshot(),
        "cost_version": COST_VERSION,
        "cost_assumptions": COST_ASSUMPTIONS,
        "research_plan": plan_d,
        "params": {
            "step": step,
            "max_codes": max_codes,
            "horizon": horizon,
            "exit": {
                "mode": "fixed",
                "stop_pct": stop_pct,
                "target_pct": target_pct,
                "max_hold": max_hold,
            },
        },
        "is": is_m,
        "oos": oos_m,
        "promotion": promotion,
        "baseline_note": baseline_note,
        "can_claim_edge": bool(
            promotion.get("promotable") and mode == "full" and plan_d.get("data_ready_for_edge_validation")
        ),
        "disclaimer": (
            "研究辅助，不是投资建议。"
            "本报告为形态基线抽样证据，不等于 Lab 网格最优、不等于 A 池可交易名单。"
        ),
    }
    return report


def render_evidence_markdown(report: dict[str, Any]) -> str:
    plan = report.get("research_plan") or {}
    is_m = report.get("is") or {}
    oos_m = report.get("oos") or {}
    promo = report.get("promotion") or {}
    lines = [
        "# 可信证据报告（形态基线 · ENTRY v1）",
        "",
        f"- 生成时间: `{report.get('generated_at')}`",
        f"- 入场定义: `{report.get('entry_definition_id')}`",
        f"- 成本版本: `{report.get('cost_version')}`",
        f"- 研究模式: **{plan.get('mode')}**",
        f"- IS: `{plan.get('is_start')}` ~ `{plan.get('is_end')}`",
        f"- OOS: `{plan.get('oos_start')}` ~ `{plan.get('oos_end')}`",
        f"- 可声称 edge: **{report.get('can_claim_edge')}**",
        "",
        "## IS（样本内）",
        "",
        f"- 毛交易数: {is_m.get('n_trades')}  胜率: {is_m.get('win_rate')}  PF: {is_m.get('profit_factor')}",
        f"- 净成交数: {is_m.get('net_n_trades')}  净胜率: {is_m.get('net_win_rate')}  "
        f"净PF: {is_m.get('net_profit_factor')}  净回撤: {is_m.get('net_max_drawdown')}",
        "",
        "## OOS（样本外）",
        "",
        f"- 毛交易数: {oos_m.get('n_trades')}  胜率: {oos_m.get('win_rate')}  PF: {oos_m.get('profit_factor')}",
        f"- 净成交数: {oos_m.get('net_n_trades')}  净胜率: {oos_m.get('net_win_rate')}  "
        f"净PF: {oos_m.get('net_profit_factor')}  净回撤: {oos_m.get('net_max_drawdown')}",
        "",
        "## 晋级门禁",
        "",
        f"```json\n{json.dumps(promo, ensure_ascii=False, indent=2)}\n```",
        "",
        f"基线说明: {report.get('baseline_note')}",
        "",
        str(report.get("disclaimer") or ""),
        "",
    ]
    return "\n".join(lines)


def write_evidence_report(report: dict[str, Any], out_dir: str | Path | None = None) -> dict[str, str]:
    """落盘前校验入场定义指纹：声明与当前注册表不符 → 拒绝生成（fail-closed）。"""
    verify_report_entry_fingerprint(report)
    out = Path(out_dir or OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    json_path = out / f"evidence_report_{stamp}.json"
    md_path = out / f"evidence_report_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_evidence_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}
