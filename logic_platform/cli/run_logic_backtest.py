"""Phase 3 闭环 CLI：模板 → 回测 → 闸门（docs §6.4/§6.5/§10）。

核心逻辑在 logic_platform/backtest/pipeline.run_pipeline（与 API 共用）。

用法：
  C:\\Python314\\python.exe -m logic_platform.cli.run_logic_backtest --template vol_breakout_v1
  C:\\Python314\\python.exe -m logic_platform.cli.run_logic_backtest --template pullback_volume_v1 \
      --start 20250101 --end 20260731 --max-codes 200 --workers 6 \
      --set exit.stop_pct=0.08 --set exit.target_pct=0.15 \
      --gate min_trades=20 --json runtime/logic_bt_result.json

退出码：闸门通过 = 0，未通过 = 1。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from logic_platform.backtest.pipeline import run_pipeline
from logic_platform.dsl.parser import list_templates

_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("logic.backtest")


def main() -> int:
    ap = argparse.ArgumentParser(description="DSL 策略闭环：模板→回测→闸门")
    ap.add_argument("--template", default="vol_breakout_v1",
                    help=f"模板 id（可用: {list_templates()}）")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-codes", type=int, default=None)
    ap.add_argument("--step", type=int, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--set", action="append", default=[],
                    help="覆盖模板参数，如 --set exit.stop_pct=0.08")
    ap.add_argument("--gate", action="append", default=[],
                    help="覆盖闸门阈值，如 --gate min_trades=20 --gate max_drawdown=0.4")
    ap.add_argument("--json", default=None, help="结果 JSON 路径（默认 runtime/logic_bt_result.json）")
    args = ap.parse_args()

    log.info("══ 闭环开始 ══ 模板=%s", args.template)
    params = {k: v for k, v in {
        "start": args.start, "end": args.end, "max_codes": args.max_codes,
        "step": args.step, "workers": args.workers,
    }.items() if v is not None}

    result = run_pipeline(args.template, params_overrides=params,
                          set_overrides=args.set, gate_overrides=args.gate)
    if "error" in result:
        log.error("闭环失败: %s", result.get("errors"))
        print(f"❌ 参数错误: {result.get('errors')}")
        return 2

    for w in result["scan_warnings"][:8]:
        log.warning("scan-warning: %s", w)
    for e in result["errors"][:8]:
        log.error("scan-error: %s", e)
    log.info("闸门: status=%s passed=%s", result["status"], result["gate_passed"])
    for c in result["gates"].get("checks", []):
        log.info("  gate %s", c.get("msg"))

    out_path = Path(args.json) if args.json else _ROOT / "runtime" / "logic_bt_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("结果已写 %s（%.1fs）", out_path, result.get("elapsed_sec", 0))

    m = result["metrics"]
    print("\n════ 闭环结果 ════")
    print(f"策略      : {result['strategy_id']} v{result['version']} {result['name']}")
    print(f"状态      : {result['status']}（闸门 {'通过 ✅' if result['gate_passed'] else '未通过 ❌'}）")
    print(f"区间      : {result['params']['start']} ~ {result['params']['end']}  采样步长 {result['params']['step']}")
    print(f"信号/交易 : {result['signals_count']} / {m.get('n_trades')}")
    print(f"总收益    : {m.get('total_return')}   平均收益 {m.get('avg_ret')}")
    print(f"胜率      : {m.get('win_rate')}   盈亏比 {m.get('profit_factor')}")
    print(f"最大回撤  : {m.get('max_drawdown')}   平均持有 {m.get('avg_hold_days')}d")
    print(f"出场分布  : {m.get('exits')}")
    print(f"run_id    : {result['run_id']}（已落库 logic_strategies / logic_backtests）")
    return 0 if result["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
