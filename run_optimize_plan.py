"""运行方案 X 的 IS 网格 + OOS 验证（脚本文件方式——Windows spawn 需要正常模块入口）。

用法: python run_optimize_plan.py A
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from walkforward import run_is_oos  # noqa: E402


def main() -> int:
    strategy = sys.argv[1] if len(sys.argv) > 1 else "A"
    max_codes = int(sys.argv[2]) if len(sys.argv) > 2 else 4500
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    print(f"[run_optimize_plan] strategy={strategy} max_codes={max_codes} step={step}", flush=True)
    r = run_is_oos(strategy=strategy, step=step, max_codes=max_codes, top_n=3,
                   is_start="20250101", is_end="20251231",
                   oos_start="20260101", oos_end="20260803",
                   progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}", flush=True))
    out = {
        "is_top": r["is"].head(8).to_dict("records") if not r["is"].empty else [],
        "oos": r["oos"].to_dict("records") if not r["oos"].empty else [],
        "msg": r.get("msg"),
    }
    os.makedirs("runtime", exist_ok=True)
    path = os.path.join("runtime", f"is_oos_{strategy}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"DONE_{strategy} saved {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
