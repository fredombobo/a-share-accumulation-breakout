"""扫描子进程入口 —— 可被 taskkill 整树杀掉，保证取消一定生效。

用法（由 backend 调用，勿手跑）:
  python scan_job_runner.py --task-id xxx --top 20 --days 160 \\
      --progress runtime/scan_xxx.progress.json \\
      --result runtime/scan_xxx.result.json \\
      --cancel-file runtime/scan_xxx.cancel
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

# 清理代理污染
for _k in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "PYTHONPATH",
):
    os.environ.pop(_k, None)

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            if os.name != "nt" or winerror not in (5, 32) or attempt == 7:
                raise
            # Windows readers do not share DELETE by default. The backend poller
            # holds the target only briefly, so wait for that handle to close.
            time.sleep(0.01 * (attempt + 1))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task-id", required=True)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--days", type=int, default=160)
    p.add_argument("--progress", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--cancel-file", type=Path, required=True)
    args = p.parse_args()

    def cancelled() -> bool:
        try:
            return args.cancel_file.exists()
        except Exception:  # noqa: BLE001
            return False

    def progress_cb(stage: str, pct: int, msg: str = "") -> None:
        _write_json(args.progress, {
            "task_id": args.task_id,
            "stage": stage,
            "progress": int(pct),
            "message": msg or "",
            "cancelled": cancelled(),
        })

    progress_cb("数据准备", 3, "子进程启动")
    if cancelled():
        _write_json(args.result, {"cancelled": True, "status": "cancelled"})
        return 0

    try:
        import run_screener

        result = run_screener.run_scan(
            top=args.top,
            days=args.days,
            force=False,
            progress_cb=progress_cb,
            cancel_check=cancelled,
        )
        if cancelled() or (isinstance(result, dict) and result.get("cancelled")):
            _write_json(args.result, {"cancelled": True, "status": "cancelled"})
            progress_cb("已取消", 100, "子进程收到取消")
            return 0

        df_a = result.get("df_a") if isinstance(result, dict) else None
        df_b = result.get("df_b") if isinstance(result, dict) else None
        count_a = 0 if df_a is None or getattr(df_a, "empty", True) else len(df_a)
        count_b = 0 if df_b is None or getattr(df_b, "empty", True) else len(df_b)
        out = {
            "cancelled": False,
            "status": "ok",
            "latest_date": result.get("latest_date"),
            "total_candidates": result.get("total_candidates", 0),
            "hits": len(result.get("hits") or []),
            "count": count_a,
            "count_a": count_a,
            "count_b": count_b,
            "regime": result.get("regime"),
            "freshness": result.get("freshness"),
            "pool_report": result.get("pool_report"),
            "elapsed_sec": result.get("elapsed_sec"),
            "workers": result.get("workers"),
        }
        _write_json(args.result, out)
        progress_cb("完成", 100, f"A={count_a} B={count_b}")
        return 0
    except Exception as e:  # noqa: BLE001
        _write_json(args.result, {
            "cancelled": False,
            "status": "error",
            "error": str(e)[:800],
            "trace": traceback.format_exc()[-1500:],
        })
        progress_cb("失败", 100, str(e)[:120])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
