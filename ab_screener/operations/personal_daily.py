"""Personal daily receipts and explicit backups; never touches paper ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.operations.backup import create_backup


def record_scan(db: Path, result_path: Path) -> dict[str, Any]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    task_id = result_path.name.removeprefix("scan_").removesuffix(".result.json")
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        job = conn.execute("SELECT status FROM scan_jobs WHERE task_id=?", (task_id,)).fetchone()
        scan = conn.execute("SELECT git_sha,as_of FROM scan_runs WHERE task_id=? AND status='SUCCEEDED'", (task_id,)).fetchone()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.strftime("%Y%m%d")
        # Canonical local calendar is single-exchange (Tushare's SSE default),
        # keyed by cal_date; it deliberately has no exchange column.
        calendar_end = conn.execute("SELECT MAX(cal_date) FROM trade_cal").fetchone()[0]
        operator = "<" if (now.hour, now.minute) < (16, 15) else "<="
        expected = conn.execute(
            f"SELECT MAX(cal_date) FROM trade_cal WHERE is_open=1 AND cal_date {operator} ?", (today,),
        ).fetchone()[0]
    from build_version import build_version

    if not scan or scan[0] != build_version() or scan[1] != latest:
        raise ValueError("扫描构建/日期不属于当前版本，不补签完成记录")
    if not job or job[0] != "SUCCEEDED" or result.get("status") != "ok":
        raise ValueError("扫描未成功，不能生成日用完成记录")
    if not latest or result.get("latest_date") != latest:
        raise ValueError("扫描日期与行情不一致，不能生成日用完成记录")
    if not calendar_end or calendar_end < today or latest != expected:
        raise ValueError("行情不是最新已完成交易日")
    payload = {
        "schema": "personal-daily-v1", "product": "accumulation_breakout",
        "trade_date": latest, "task_id": task_id, "status": "DAILY_COMPLETE",
        "build_version": scan[0],
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "live_trading_enabled": False,
        "note": "个人日用验收，不是机构 soak，不证明策略有效；每交易日仅记录一次。",
    }
    folder = db.parent / "personal-daily"
    folder.mkdir(exist_ok=True)
    target = folder / f"{latest}.json"
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        return json.loads(target.read_text(encoding="utf-8"))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--scan-result", type=Path)
    action.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    if args.scan_result:
        result = record_scan(args.db, args.scan_result)
    else:
        result = create_backup(args.db, args.backup_root, compressed=True)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
