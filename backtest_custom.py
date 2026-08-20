"""自定义参数 → 净成本样本内/样本外（IS/OOS）回测入口
======================================================

目的：输入自己的出场参数（及可选形态阈值），系统在自动选择（或手动指定）
的 IS/OOS 窗口上回放，输出**净成本**指标、OOS/IS 保持率、三窗 walk-forward
复核，并登记试验历史（防自我选择偏差）。

用法：
  # 只改出场参数（其余取 config 默认）
  python backtest_custom.py --vol-ratio-min 1.6 --stop-pct 0.07 \
      --exit-window 10 --strong-reset 3 --max-codes 600 --step 10

  # 同时覆盖形态阈值（箱体/突破）
  python backtest_custom.py --box-max-amp 0.30 --breakout-vol-ratio 1.8 \
      --breakout-chg-min 0.03 --max-codes 600

  # 自定义窗口（覆盖自动窗；格式 YYYYMMDD:YYYYMMDD）
  python backtest_custom.py ... --is 20230801:20250731 --oos 20250801:20260731

  # 查看试验历史（防止反复调参）
  python backtest_custom.py --history

口径说明（与报告 disclosures 一致）：
  - 入场：ENTRY-DEFINITION-V1（突破日下一交易日开盘），见 ab_screener/domain/entry_definition.py
  - 出场：trade_sim（bench 标杆量出场；B 方案为五步抓主升）
  - 成本：ab_screener.domain.costs 统一口径（佣金/印花税/其他费/滑点）
  - 统计：net_* 为净成本口径；gross 指标仅供对照
  - 宇宙：research_universe（当前上市名单快照）→ 存在幸存者偏差，见披露
  - 单次试验不做多重比较校正：同一参数反复试跑会使 OOS 结论失真，
    试验历史会如实显示该参数已试次数

输出：runtime/custom_bt_<report_id>.json / .md，试验历史 runtime/custom_bt_history.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import (
    BENCH_EXIT_WINDOW,
    BENCH_STOP_PCT,
    BENCH_STRONG_RESET,
    BENCH_VOL_RATIO_MIN,
    BT_MIN_TRADES,
    WF_MIN_OOS_PF_RATIO,
)
from research_windows import recommend_research_plan
from walkforward import (
    ELIG_MAX_NET_DRAWDOWN,
    ELIG_MIN_NET_WIN_RATE,
    run_is_oos,
    wf_recheck,
)

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "runtime" / "custom_bt_history.jsonl"

# CLI 暴露的形态阈值 → detect_accumulation_breakout kwargs
SIGNAL_KWARG_NAMES = {
    "--box-min-days": "box_min_days",
    "--box-max-days": "box_max_days",
    "--box-max-amp": "box_max_amp",
    "--breakout-vol-ratio": "breakout_vol_ratio",
    "--breakout-chg-min": "breakout_chg_min",
    "--breakout-chg-max": "breakout_chg_max",
    "--breakout-window-days": "breakout_window_days",
    "--box-max-mid-drawdown": "box_max_mid_drawdown",
    "--pos-trend-max-drop": "pos_trend_max_drop",
    "--breakout-vs-recent-vol-ratio": "breakout_vs_recent_vol_ratio",
    "--require-structure": "require_structure",
}


def report_id(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def parse_window(raw: str | None) -> tuple[str, str] | None:
    """解析 YYYYMMDD:YYYYMMDD；无效返回 None。"""
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        raise SystemExit(f"窗口格式错误（应为 YYYYMMDD:YYYYMMDD）: {raw}")
    a = "".join(ch for ch in parts[0] if ch.isdigit())
    b = "".join(ch for ch in parts[1] if ch.isdigit())
    if len(a) != 8 or len(b) != 8 or a >= b:
        raise SystemExit(f"窗口区间无效: {raw}（start < end 且各 8 位数字）")
    return a, b


def _f(value: Any, digits: int = 4) -> float | None:
    """None 安全浮点；NaN 视为 None。"""
    if value is None:
        return None
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    if fv != fv:  # NaN
        return None
    return round(fv, digits)


def _native(value: Any) -> Any:
    """递归转 JSON 可序列化的原生类型（numpy 标量/DataFrame 残留）。"""
    if value is None:
        return None
    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.ndarray):
            return [_native(v) for v in value.tolist()]
    except ImportError:
        pass
    if isinstance(value, dict):
        return {str(k): _native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(v) for v in value]
    return value


def load_history() -> list[dict]:
    if not HISTORY_PATH.is_file():
        return []
    rows = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_history(entry: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _to_md(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"# 自定义参数 IS/OOS 回测报告 `{report['report_id']}`")
    lines.append("")
    lines.append(f"- 生成时间：{report['created_at']}")
    lines.append(f"- 数据最新交易日：{report['data']['max_trade_date']}（{report['data']['n_dates']} 个交易日）")
    lines.append(f"- 研究模式：{report['windows']['mode']}（可声称 edge：{'是' if report['windows']['can_claim_edge'] else '否'}）")
    lines.append(f"- 宇宙：{report['data']['universe_n']} 只（当前上市名单快照）")
    lines.append("")
    lines.append("## 参数")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report["params"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 窗口")
    lines.append("")
    w = report["windows"]
    lines.append(f"- IS : {w['is_start']} ~ {w['is_end']}")
    lines.append(f"- OOS: {w['oos_start']} ~ {w['oos_end']}")
    lines.append("")
    lines.append("## 结果（净成本口径）")
    lines.append("")
    lines.append("| 指标 | IS | OOS |")
    lines.append("|------|----|-----|")
    rows = [
        ("交易数", "is_net_n_trades", "oos_net_n_trades"),
        ("未成交", "is_net_unfilled", "oos_net_unfilled"),
        ("净胜率", "is_net_win_rate", "oos_net_win_rate"),
        ("净盈亏比 PF", "is_net_profit_factor", "oos_net_profit_factor"),
        ("净平均收益", "is_net_avg_return", "oos_net_avg_return"),
        ("净最大回撤", "is_net_max_drawdown", "oos_net_max_drawdown"),
        ("总佣金", "is_commission", "oos_commission"),
        ("印花税", "is_stamp_tax", "oos_stamp_tax"),
        ("滑点成本", "is_slippage_cost", "oos_slippage_cost"),
        ("毛交易数", "is_n_trades", "oos_n_trades"),
        ("毛胜率", "is_win_rate", "oos_win_rate"),
        ("毛 PF", "is_profit_factor", "oos_profit_factor"),
    ]
    for label, is_key, oos_key in rows:
        lines.append(
            f"| {label} | {report['is'].get(is_key)} | {report['oos'].get(oos_key)} |"
        )
    lines.append("")
    hr = report.get("hold_ratio") or {}
    if hr.get("pf") is not None:
        lines.append(f"- OOS/IS PF 保持率：{hr['pf']}（阈值 ≥ {WF_MIN_OOS_PF_RATIO}）")
    if hr.get("win_rate") is not None:
        lines.append(f"- OOS/IS 胜率保持率：{hr['win_rate']}")
    lines.append("")
    if report.get("wf"):
        wf = report["wf"]
        lines.append("## Walk-forward 复核")
        lines.append("")
        lines.append(f"- wf_pass: {wf.get('wf_pass')}")
        lines.append(f"- 训练窗平均 PF: {wf.get('train_mean_pf')} / 测试窗平均 PF: {wf.get('oos_mean_pf')}")
        for d in wf.get("wf_detail") or []:
            lines.append(
                f"  - {d.get('window')}: train_pf={d.get('train_pf')} test_pf={d.get('test_pf')} "
                f"test_dd={d.get('test_dd')} test_wr={d.get('test_wr')} test_n={d.get('test_n')}"
            )
        lines.append("")
    lines.append("## 门禁（与 ab_screener 可信门禁同口径）")
    lines.append("")
    for gate in report["gates"]:
        mark = "✅" if gate.get("pass") else ("—" if gate.get("pass") is None else "❌")
        lines.append(f"- {mark} {gate['name']}: {gate.get('value')}（{gate['criteria']}）")
    lines.append("")
    lines.append("## 披露（务必阅读）")
    lines.append("")
    for d in report["disclosures"]:
        lines.append(f"- {d}")
    return "\n".join(lines)


def print_console(report: dict) -> None:
    print("=" * 62)
    print(f"  自定义参数 IS/OOS 回测  {report['report_id']}")
    print("=" * 62)
    w = report["windows"]
    print(f"  窗口: IS {w['is_start']}~{w['is_end']} | OOS {w['oos_start']}~{w['oos_end']} (mode={w['mode']})")
    print(f"  数据: 最新交易日 {report['data']['max_trade_date']} | 宇宙 {report['data']['universe_n']} 只")
    print("-" * 62)
    print(f"  {'指标':<14}{'IS':>12}{'OOS':>12}")
    is_row, oos_row = report["is"], report["oos"]
    for label, is_key, oos_key in (
        ("净交易数", "is_net_n_trades", "oos_net_n_trades"),
        ("净胜率", "is_net_win_rate", "oos_net_win_rate"),
        ("净PF", "is_net_profit_factor", "oos_net_profit_factor"),
        ("净均收益", "is_net_avg_return", "oos_net_avg_return"),
        ("净最大回撤", "is_net_max_drawdown", "oos_net_max_drawdown"),
        ("毛交易数", "is_n_trades", "oos_n_trades"),
    ):
        is_v = str(is_row.get(is_key))
        oos_v = str(oos_row.get(oos_key))
        print(f"  {label:<14}{is_v:>12}{oos_v:>12}")
    hr = report.get("hold_ratio") or {}
    if hr.get("pf") is not None:
        print(f"  OOS/IS PF 保持率: {hr['pf']}（阈值 ≥{WF_MIN_OOS_PF_RATIO}）")
    if report.get("wf"):
        wf = report["wf"]
        print(f"  WF 复核: pass={wf.get('wf_pass')} train_mean_pf={wf.get('train_mean_pf')} "
              f"oos_mean_pf={wf.get('oos_mean_pf')}")
    print("-" * 62)
    for gate in report["gates"]:
        mark = "✅" if gate.get("pass") else ("—" if gate.get("pass") is None else "❌")
        print(f"  {mark} {gate['name']}: {gate.get('value')}  {gate['criteria']}")
    print("-" * 62)
    for d in report["disclosures"]:
        print(f"  · {d}")
    print(f"  报告: runtime/custom_bt_{report['report_id']}.md/.json")
    print("=" * 62)


def run_backtest(args: Any) -> dict:
    plan = recommend_research_plan()
    if plan.mode == "insufficient":
        raise SystemExit(
            f"数据不足（{plan.n_dates} 交易日），无法做 IS/OOS。请先 python sync_history.py\n" + "\n".join(plan.notes)
        )

    is_start, is_end = parse_window(getattr(args, "is", None)) or (plan.is_start, plan.is_end)
    oos_start, oos_end = parse_window(args.oos) or (plan.oos_start, plan.oos_end)

    exit_params = {
        "vol_ratio_min": args.vol_ratio_min,
        "stop_pct": args.stop_pct,
        "exit_window": args.exit_window,
        "strong_reset": args.strong_reset,
    }
    signal_kwargs: dict[str, Any] = {}
    for kw in SIGNAL_KWARG_NAMES.values():
        value = getattr(args, kw, None)
        if value is not None:
            signal_kwargs[kw] = value
    # v2 突破逻辑参数（布尔语义与 detect 参数一致）
    if getattr(args, "no_ma60", False):
        signal_kwargs["require_ma60"] = False
    if getattr(args, "max_pullbacks", None) is not None:
        signal_kwargs["max_pullbacks"] = int(args.max_pullbacks)

    params_blob = {
        "strategy": args.strategy,
        "exit": exit_params,
        "signal": signal_kwargs or None,
    }
    rid = report_id(params_blob)

    history = load_history()
    this_param_trials = sum(1 for h in history if h.get("report_id") == rid)

    def progress(msg: str, pct: int) -> None:
        print(f"[{int(pct):3d}%] {msg}", flush=True)

    print(f"[自定义回测] report_id={rid} strategy={args.strategy} "
          f"max_codes={args.max_codes} step={args.step}", flush=True)
    if plan.mode == "degraded":
        print("[自定义回测] WARN: degraded 窗口——结果仅供摸底，不可当 edge", flush=True)

    result = run_is_oos(
        strategy=args.strategy,
        step=args.step,
        max_codes=args.max_codes,
        single=exit_params,
        signal_kwargs=signal_kwargs or None,
        is_start=is_start,
        is_end=is_end,
        oos_start=oos_start,
        oos_end=oos_end,
        progress_cb=progress,
    )
    is_row = _native(result["is"].iloc[0].to_dict()) if not result["is"].empty else {}
    oos_row = _native(result["oos"].iloc[0].to_dict()) if not result["oos"].empty else {}
    # run_is_oos 的 IS 行是裸键（net_n_trades 等）、OOS 行带 oos_ 前缀；
    # 统一报告视图：report["is"] 用 is_ 前缀，report["oos"] 保持 oos_ 前缀。
    is_view = {f"is_{k}": v for k, v in is_row.items()}

    # WF 复核：仅 full 模式且窗口完整时执行
    wf: dict | None = None
    if plan.mode == "full" and plan.wf_windows:
        combo = {"strategy": args.strategy, **exit_params}
        wf_df = wf_recheck(
            [combo],
            step=args.step,
            max_codes=args.max_codes,
            windows=plan.wf_windows,
            progress_cb=progress,
            signal_kwargs=signal_kwargs or None,
        )
        if not wf_df.empty:
            wf = _native(wf_df.iloc[0].to_dict())
        else:
            wf = {"wf_pass": False, "evidence_complete": False, "wf_detail": []}
    else:
        print("[自定义回测] 跳过 WF 复核（非 full 模式）", flush=True)

    # 数据新鲜度
    from local_store import LocalStore

    store = LocalStore()
    max_date = store.max_trade_date("daily")

    # 保持率
    is_pf = _f(is_row.get("net_profit_factor"))
    oos_pf = _f(oos_row.get("oos_net_profit_factor"))
    is_wr = _f(is_row.get("net_win_rate"))
    oos_wr = _f(oos_row.get("oos_net_win_rate"))
    hold_ratio = {
        "pf": round(oos_pf / is_pf, 3) if is_pf and oos_pf is not None else None,
        "win_rate": round(oos_wr / is_wr, 3) if is_wr and oos_wr is not None else None,
    }

    oos_n = _f(oos_row.get("oos_net_n_trades"), 0)
    oos_dd = _f(oos_row.get("oos_net_max_drawdown"))
    gates = [
        {
            "name": "OOS 净交易数",
            "value": oos_n,
            "criteria": f"≥ {BT_MIN_TRADES}",
            "pass": None if oos_n is None else oos_n >= BT_MIN_TRADES,
        },
        {
            "name": "OOS 净胜率",
            "value": oos_wr,
            "criteria": f"≥ {ELIG_MIN_NET_WIN_RATE}",
            "pass": None if oos_wr is None else oos_wr >= ELIG_MIN_NET_WIN_RATE,
        },
        {
            "name": "OOS 净最大回撤",
            "value": oos_dd,
            "criteria": f"≤ {ELIG_MAX_NET_DRAWDOWN}",
            "pass": None if oos_dd is None else oos_dd <= ELIG_MAX_NET_DRAWDOWN,
        },
        {
            "name": "OOS/IS PF 保持率",
            "value": hold_ratio["pf"],
            "criteria": f"≥ {WF_MIN_OOS_PF_RATIO}",
            "pass": None if hold_ratio["pf"] is None else hold_ratio["pf"] >= WF_MIN_OOS_PF_RATIO,
        },
        {
            "name": "WF 三窗复核",
            "value": (wf or {}).get("wf_pass"),
            "criteria": "三窗 test_pf 平均 ≥ 阈值 且 DD≤25% 且 n≥30",
            "pass": None if wf is None else bool((wf or {}).get("wf_pass")),
        },
    ]

    total_trials = len(history) + 1
    disclosures = [
        "宇宙为「上市 + 退市全历史」名单（research_universe include_delisted=True），已消除幸存者偏差；退市股参与历史回测，结果比只看当前上市更保守可信。",
        f"单次试验不做多重比较校正；本参数（report_id={rid}）历史已试 {this_param_trials} 次、全部试验累计 {total_trials} 次。反复调参跑 OOS 会污染结论，请以首次无预期结果为准。",
        "成本口径：佣金万五（最低 5 元/边）、卖出印花税千一、其他费万一、滑点万十（ab_screener/domain/costs.py）。",
        "入场为 ENTRY-DEFINITION-V1：突破日下一交易日开盘（无 open 用 close）；突破日收盘判定信号。",
        "本报告为个人研究辅助，不是投资建议。",
    ]

    report = {
        "report_id": rid,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "params": params_blob,
        "windows": {
            "mode": plan.mode,
            "is_start": is_start,
            "is_end": is_end,
            "oos_start": oos_start,
            "oos_end": oos_end,
            "can_claim_edge": plan.can_claim_edge,
        },
        "data": {
            "max_trade_date": max_date,
            "n_dates": plan.n_dates,
            "universe_n": args.max_codes,
        },
        "is": is_view,
        "oos": oos_row,
        "hold_ratio": hold_ratio,
        "wf": wf,
        "gates": gates,
        "disclosures": disclosures,
    }

    out_json = ROOT / "runtime" / f"custom_bt_{rid}.json"
    out_md = ROOT / "runtime" / f"custom_bt_{rid}.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(_to_md(report), encoding="utf-8")

    append_history({
        "ts": report["created_at"],
        "report_id": rid,
        "params": params_blob,
        "window": f"{is_start}:{is_end}|{oos_start}:{oos_end}",
        "mode": plan.mode,
        "oos_net_n_trades": oos_n,
        "oos_net_win_rate": oos_wr,
        "oos_net_profit_factor": oos_pf,
        "oos_net_max_drawdown": oos_dd,
        "hold_pf": hold_ratio["pf"],
        "wf_pass": (wf or {}).get("wf_pass"),
    })

    print_console(report)
    return report


def report_snapshot_universe() -> str:
    """报告中宇宙快照的描述（避免重复代码分支）。"""
    return "research_universe：上市状态 L、排序后取前 N 只，排除 4/8/92 前缀"


def show_history() -> None:
    history = load_history()
    if not history:
        print("尚无试验历史。")
        return
    print(f"共 {len(history)} 次自定义回测试验：")
    print(f"  {'时间':<20}{'report_id':<18}{'OOS交易':>8}{'OOS胜率':>9}{'OOS_PF':>9}{'OOS回撤':>9}{'PF保持':>8}{'WF':>5}")
    for h in history:
        ts = str(h.get("ts", ""))[:19]
        rid = str(h.get("report_id", ""))
        n = str(h.get("oos_net_n_trades"))
        wr = str(h.get("oos_net_win_rate"))
        pf = str(h.get("oos_net_profit_factor"))
        dd = str(h.get("oos_net_max_drawdown"))
        hp = str(h.get("hold_pf"))
        wfp = str(h.get("wf_pass"))
        print(
            f"  {ts:<20}{rid:<18}{n:>8}{wr:>9}{pf:>9}{dd:>9}{hp:>8}{wfp:>5}"
        )
    by_id: dict[str, int] = {}
    for h in history:
        by_id[h.get("report_id", "?")] = by_id.get(h.get("report_id", "?"), 0) + 1
    repeats = {k: v for k, v in by_id.items() if v > 1}
    if repeats:
        print("\n⚠ 以下参数被反复试跑（存在选择偏差风险）：")
        for rid2, cnt in sorted(repeats.items(), key=lambda kv: -kv[1]):
            print(f"  {rid2}: {cnt} 次")


def main() -> int:
    p = argparse.ArgumentParser(
        description="自定义参数 → 净成本 IS/OOS 回测（ENTRY v1，见文件头说明）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--history", action="store_true", help="查看试验历史后退出")
    p.add_argument("--strategy", default="A", choices=["A", "B"], help="A=横盘吸筹突破 B=五步抓主升")
    # 出场参数
    p.add_argument("--vol-ratio-min", type=float, default=BENCH_VOL_RATIO_MIN, help="建仓放量阈值（量/5日均量）")
    p.add_argument("--stop-pct", type=float, default=BENCH_STOP_PCT, help="兜底止损比例")
    p.add_argument("--exit-window", type=int, default=BENCH_EXIT_WINDOW, help="标杆量出货计数窗口（交易日）")
    p.add_argument("--strong-reset", type=int, default=BENCH_STRONG_RESET, help="连续强势日清零出货计数")
    # 形态阈值（可选覆盖）
    p.add_argument("--box-min-days", type=int, default=None, help="横盘最短交易日")
    p.add_argument("--box-max-days", type=int, default=None, help="横盘最长交易日")
    p.add_argument("--box-max-amp", type=float, default=None, help="箱体振幅上限")
    p.add_argument("--breakout-vol-ratio", type=float, default=None, help="突破日量/横盘均量下限")
    p.add_argument("--breakout-chg-min", type=float, default=None, help="突破日最小涨幅")
    p.add_argument("--breakout-chg-max", type=float, default=None, help="突破日最大涨幅")
    p.add_argument("--breakout-window-days", type=int, default=None, help="突破确认窗口")
    p.add_argument("--box-max-mid-drawdown", type=float, default=None, help="箱体中轴最大回撤（防下跌中继）")
    p.add_argument("--pos-trend-max-drop", type=float, default=None, help="近60日跌幅下限（负值）")
    p.add_argument("--breakout-vs-recent-vol-ratio", type=float, default=None, help="突破日量/前5日均量下限")
    p.add_argument("--require-structure", action="store_true", default=None, help="要求支撑/压力触及结构")
    p.add_argument("--no-ma60", action="store_true", help="关闭 MA60 过滤（默认 strict 开启）")
    p.add_argument("--max-pullbacks", type=int, default=None, help="突破后允许跌破箱体上沿次数（默认 strict=0）")
    # 采样与窗口
    p.add_argument("--max-codes", type=int, default=600, help="宇宙股票数（越大越慢）")
    p.add_argument("--step", type=int, default=10, help="采样步长（交易日）")
    p.add_argument("--is", default=None, help="覆盖样本内窗口 YYYYMMDD:YYYYMMDD")
    p.add_argument("--oos", default=None, help="覆盖样本外窗口 YYYYMMDD:YYYYMMDD")
    args = p.parse_args()

    if args.history:
        show_history()
        return 0

    run_backtest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
