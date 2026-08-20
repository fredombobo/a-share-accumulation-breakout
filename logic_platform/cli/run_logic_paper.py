"""Phase 5 纸交易闭环（docs §10.3 观察池 + 后验看板，MVP）。

两条能力（均遵守 §1.3"生成逻辑不直接当买卖指令"与 §6.5 闸门约束）：

1. --mode signals  ：gated 策略当日信号 → 观察卡片 JSON
   （runtime/logic_paper_signals/YYYYMMDD/*.json，research 语义，
   不投宿主 paper 下单；后续 A 池对接需人工+闸门，见 §6.4）
2. --mode backfill ：gated 策略历史信号回放 → 后验命中率报表
   （signal_date 后 5/10 交易日实际涨跌命中率 + 分状态命中率，
   与模型 p_up 对照——校准研究）

硬约束：require_gate_for_paper=true —— 仅 status=gated 策略可投递，
其余直接拒绝并说明。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from logic_platform.data.ab_store import ABStore
from logic_platform.data.strategy_repo import list_strategies
from logic_platform.dsl.interpreter import Interpreter
from logic_platform.dsl.parser import load_template

_ROOT = Path(__file__).resolve().parents[2]
PAPER_DIR = _ROOT / "runtime" / "logic_paper_signals"
TZ = None

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("logic.paper")


def _gated_strategies(store) -> list[str]:
    """库内 status=gated 的策略 id（硬约束：仅 gated 可投递）。"""
    return [s["id"] for s in list_strategies(store.db_path) if s["status"] == "gated"]


def _pick_strategy(store, strategy_id: str | None) -> str:
    if strategy_id:
        st = next((s for s in list_strategies(store.db_path)
                   if s["id"] == strategy_id), None)
        if st is None:
            raise SystemExit(f"策略不存在: {strategy_id}")
        if st["status"] != "gated":
            raise SystemExit(
                f"策略 {strategy_id} 状态为 {st['status']}（非 gated）——"
                "硬约束 require_gate_for_paper：未过闸门禁止投递")
        return strategy_id
    gated = _gated_strategies(store)
    if not gated:
        raise SystemExit("库内无 gated 策略（先用 run_logic_backtest 跑出过闸门的策略）")
    return gated[0]


def mode_signals(strategy_id: str, max_codes: int, workers: int, as_of: str | None) -> int:
    """当日信号 → 观察卡片。"""
    store = ABStore()
    dsl = load_template(strategy_id)
    codes = store.universe_from_stock_basic()[: max_codes]

    # 只评估 as_of 当日（近窗 lookback 由解释器内部取）
    scan = Interpreter().run(dsl, codes, store, workers=workers)
    signals = scan["signals"]
    # 只保留 as_of 最近的（当日观察）
    latest_asof = signals[-1]["as_of"] if signals else None
    today_signals = [s for s in signals if s["as_of"] == latest_asof] if latest_asof else []
    date_tag = latest_asof.replace("-", "") if latest_asof else as_of or "none"

    day_dir = PAPER_DIR / date_tag
    day_dir.mkdir(parents=True, exist_ok=True)
    for s in today_signals:
        (day_dir / f"{s['ts_code'].replace('.', '_')}.json").write_text(
            json.dumps({
                "dsl_id": strategy_id, "as_of": s["as_of"],
                "signal_date": s["signal_date"], "state": s["state"],
                "reasons": s["reasons"], "box": s["box"],
                "research_only": True,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n════ 观察卡（{strategy_id} · {latest_asof or '—'}）════")
    print(f"信号 {len(today_signals)} 条 → {day_dir}")
    for s in today_signals[:20]:
        print(f"  {s['ts_code']:<12} {s['state']:<16} 信号日 {s['signal_date']}  {s['reasons'][0][:40] if s['reasons'] else ''}")
    print("research 语义：观察研究用，非买卖指令（§1.3）")
    return 0


def mode_backfill(strategy_id: str, max_codes: int, workers: int,
                  days_back: int, horizon: int) -> int:
    """历史信号回放 → 后验命中率。"""
    store = ABStore()
    dsl = load_template(strategy_id)
    cal = _recent_dates(store, days_back)
    if not cal:
        raise SystemExit("无可回放交易日")

    start = cal[0]
    end = cal[-1]
    codes = store.universe_from_stock_basic()[: max_codes]
    log.info("回放 %s ~ %s（%d 日，%d 只，step=5）", start, end, len(cal), len(codes))

    # 用解释器全区间扫描（采样 step=5）
    dsl.params.start = start
    dsl.params.end = end
    dsl.params.step = 5
    scan = Interpreter().run(dsl, codes, store, workers=workers)
    signals = scan["signals"]
    log.info("历史信号 %d 条", len(signals))

    # 逐信号后验：signal_date 后 horizon 日收益（用每只股票的 df）
    df_cache: dict[str, pd.DataFrame] = {}
    hits: list[dict] = []
    for s in signals:
        code = s["ts_code"]
        df = df_cache.get(code)
        if df is None:
            df = store.ohlcv(code, start=start)
            df_cache[code] = df
        if df is None or df.empty:
            continue
        sd = s["signal_date"]
        try:
            i = df.index[df["date"] == sd][0]
        except IndexError:
            continue
        fut = df.iloc[i + 1: i + 1 + horizon]
        if len(fut) < horizon:
            continue  # 窗口不足（尾部）→ 丢弃
        ret = float(fut["close"].iloc[-1] / df.iloc[i]["close"] - 1)
        hits.append({
            "ts_code": code, "signal_date": sd, "state": s["state"],
            "ret": round(ret, 4), "hit": ret > 0,
        })

    if not hits:
        print("回放无有效信号（窗口内）")
        return 1

    df = pd.DataFrame(hits)
    n = len(df)
    hit_rate = float(df["hit"].mean())
    avg_ret = float(df["ret"].mean())
    by_state = df.groupby("state").agg(
        n=("hit", "size"), hit_rate=("hit", "mean"), avg_ret=("ret", "mean"),
    ).round(4).to_dict("index")

    report = {
        "strategy_id": strategy_id, "window": {"start": start, "end": end},
        "horizon": horizon, "signals": n, "hit_rate": round(hit_rate, 4),
        "avg_ret": round(avg_ret, 4), "by_state": by_state,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "research_only": True,
    }
    out = _ROOT / "runtime" / f"logic_paper_backfill_{strategy_id}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n════ 后验命中率（{strategy_id} · {horizon} 日）════")
    print(f"信号 {n} · 命中率 {hit_rate:.1%} · 平均收益 {avg_ret:.2%}")
    for st, v in by_state.items():
        print(f"  {st:<16} n={v['n']:<5} 命中率 {v['hit_rate']:.1%}  平均 {v['avg_ret']:.2%}")
    print(f"报表: {out}")
    return 0


def _recent_dates(store, days_back: int) -> list[str]:
    try:
        cal = store._store.distinct_dates("daily")
    except Exception:  # noqa: BLE001
        return []
    return cal[-days_back:]


def main() -> int:
    ap = argparse.ArgumentParser(description="纸交易闭环：观察卡 / 后验报表")
    ap.add_argument("--mode", default="signals", choices=["signals", "backfill"])
    ap.add_argument("--strategy", default=None, help="gated 策略 id（默认库内最新 gated）")
    ap.add_argument("--max-codes", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--as-of", default=None, help="信号模式：指定评估日 YYYY-MM-DD")
    ap.add_argument("--days-back", type=int, default=240, help="回放模式：回看交易日数")
    ap.add_argument("--horizon", type=int, default=10, help="回放模式：后验窗口")
    args = ap.parse_args()

    t0 = time.time()
    store = ABStore()
    strategy_id = _pick_strategy(store, args.strategy)
    log.info("策略 %s（gated 校验通过）", strategy_id)

    if args.mode == "signals":
        rc = mode_signals(strategy_id, args.max_codes, args.workers, args.as_of)
    else:
        rc = mode_backfill(strategy_id, args.max_codes, args.workers,
                           args.days_back, args.horizon)
    log.info("完成（%.1fs）", time.time() - t0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
