"""G0 数据与测量门禁审计（只读数据库）→ runtime/research/scorecard/system/G0.json。

用法：
  .venv312\\Scripts\\python.exe scripts\\run_g0_audit.py [--db runtime/stock_data.db] [--start 20150101]
随后运行 scripts/strategy_scorecard.py 查看分数。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.data.g0_audit import run_g0_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G0 数据与测量门禁审计")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--root", default="runtime/research/scorecard")
    parser.add_argument("--start", default="20150101")
    parser.add_argument("--end")
    parser.add_argument("--sample", type=int, default=40)
    args = parser.parse_args(argv)
    try:
        evidence = run_g0_audit(Path(args.db), Path(args.root), start=args.start, end=args.end, sample=args.sample)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return 2
    print(json.dumps(evidence["metrics"], ensure_ascii=False, indent=2))
    failed = [k for k, v in evidence["metrics"].items() if not v]
    print("G0: PASS" if not failed else f"G0: 未通过 {failed}（详情见 {Path(args.root) / 'system'} 下最新 g0-* 目录）")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
