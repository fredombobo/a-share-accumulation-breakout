"""策略评分机器裁决：只由证据文件决定分数（docs/STRATEGY-SCORECARD-V0.md）。

用法：
  python scripts/strategy_scorecard.py                        # 评估并打印分数与下一步阻断
  python scripts/strategy_scorecard.py --report runtime/research/scorecard/latest.json
  python scripts/strategy_scorecard.py --import-g2 runtime/research/scorecard/<H>/event-study \\
      --hypothesis <H> [--horizon 20]                         # 事件研究产物 → G2 证据

证据根默认 runtime/research/scorecard/（运行证据，不入库、不伪造）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.research.scorecard import evaluate, g2_from_event_study

DEFAULT_ROOT = Path("runtime/research/scorecard")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="策略评分标尺 v0 机器裁决")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--report", help="裁决 JSON 另存路径")
    parser.add_argument("--import-g2", help="event_study_matched.py 输出目录（须位于证据根内）")
    parser.add_argument("--hypothesis", help="--import-g2 的目标假设 ID")
    parser.add_argument("--horizon", type=int, default=20)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.import_g2:
        if not args.hypothesis:
            parser.error("--import-g2 需要 --hypothesis")
        target = root / args.hypothesis / "G2.json"
        if target.exists():
            print(f"拒绝覆盖已存在的证据: {target}")
            return 2
        evidence = g2_from_event_study(Path(args.import_g2), root, horizon=args.horizon)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"G2 证据已写入 {target}")

    result = evaluate(root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        out = Path(args.report).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"\nSCORE={result['score']}/10")
    for blocker in result["next_blockers"]:
        print(f"  阻断: {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
