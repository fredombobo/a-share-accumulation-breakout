"""研究就绪报告（平台突破个人研究入口）

打印：日线深度、推荐 IS/OOS/WF、Token 状态、下一步动作。

用法：
  python research_status.py
  python research_status.py --json
  python research_status.py --no-token-probe
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from research_windows import research_status_dict


def main() -> int:
    p = argparse.ArgumentParser(description="平台突破研究就绪报告")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--no-token-probe", action="store_true", help="跳过 Token 网络探测")
    args = p.parse_args()
    st = research_status_dict(probe_token=not args.no_token_probe)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    plan = st["plan"]
    print("=" * 60)
    print("  A 股平台突破 · 研究就绪报告")
    print("=" * 60)
    print(f"检查时间: {st['as_of_check']}")
    print(f"日线覆盖: {plan['earliest']} ~ {plan['latest']}  ({plan['n_dates']} 交易日)")
    print(f"研究模式: {plan['mode']}  — {plan['label']}")
    print(f"  IS : {plan['is_start']} ~ {plan['is_end']}  ({plan['is_n_dates']} 日)")
    print(f"  OOS: {plan['oos_start']} ~ {plan['oos_end']}  ({plan['oos_n_dates']} 日)")
    print(f"  可声称 edge: {'是' if plan['can_claim_edge'] else '否（仅摸底）'}")
    if plan.get("wf_windows"):
        print(f"  WF 窗口数: {len(plan['wf_windows'])}")
        for i, w in enumerate(plan["wf_windows"], 1):
            print(
                f"    WF{i}: train {w['train_start']}~{w['train_end']} | "
                f"test {w['test_start']}~{w['test_end']}"
            )
    print("-" * 60)
    tok = st["token"]
    if tok.get("ok") is True:
        print("Tushare Token: 可用")
    elif tok.get("ok") is False:
        print(f"Tushare Token: 不可用 — {tok.get('error')}")
    else:
        print("Tushare Token: 未探测")
    print(f"需要历史扩容: {'是' if st['need_backfill'] else '否'}")
    if plan.get("notes"):
        print("-" * 60)
        print("说明:")
        for n in plan["notes"]:
            print(f"  · {n}")
    print("-" * 60)
    print("下一步:")
    for s in st["next_steps"]:
        print(f"  {s}")
    print("-" * 60)
    print(st["disclaimer"])
    print("=" * 60)
    # 退出码：full=0, degraded=0（可研究）, insufficient=2, token 坏且需扩容=3 仅提示
    if plan["mode"] == "insufficient":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
