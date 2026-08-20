"""P8 soak 监视器（P6.3 验收项）：收集每日完成证据到 runtime/v2/soak/<trade_date>.json。

- 不足 5 个不同完成交易日 → O-12 固定 INSUFFICIENT（不伪造等待结果）。
- 只读收集；不修改账本。
用法：
  .venv312\\Scripts\\python.exe scripts\\soak_monitor_v2.py --db runtime/stock_data.db --soak-dir runtime/v2/soak
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
MIN_SOAK_DAYS = 5


def collect_day_evidence(db_path: str | Path, trade_date: str) -> dict:
    """收集单个完成交易日的证据摘要。"""
    db_path = Path(db_path)
    evidence: dict = {
        "trade_date": trade_date,
        "collected_at": datetime.now(_TZ).isoformat(timespec="seconds"),
    }
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "daily_run_manifests" in tables:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(daily_run_manifests)").fetchall()}
            if {"status", "manifest_sha256", "created_at"} <= cols:
                row = conn.execute(
                    "SELECT status, manifest_sha256, created_at FROM daily_run_manifests"
                    " WHERE trade_date=? ORDER BY created_at DESC LIMIT 1", (trade_date,),
                ).fetchone()
                if row:
                    evidence["manifest"] = {
                        "status": row[0], "sha256": row[1], "created_at": row[2],
                    }
            elif "status" in cols:
                row = conn.execute(
                    "SELECT status FROM daily_run_manifests WHERE trade_date=?"
                    " ORDER BY rowid DESC LIMIT 1", (trade_date,),
                ).fetchone()
                if row:
                    evidence["manifest"] = {"status": row[0]}
        if "pt_daily_snapshot" in tables:
            row = conn.execute(
                "SELECT total_asset_fen, created_at FROM pt_daily_snapshot"
                " WHERE trade_date=?", (trade_date,),
            ).fetchone()
            if row:
                evidence["paper_snapshot"] = {"total_asset_fen": row[0]}
        if "dag_runs" in tables:
            row = conn.execute(
                "SELECT status, finished_at FROM dag_runs WHERE trade_date=?"
                " ORDER BY created_at DESC LIMIT 1", (trade_date,),
            ).fetchone()
            if row:
                evidence["dag"] = {"status": row[0], "finished_at": row[1]}
    return evidence


def soak_status(soak_dir: str | Path) -> dict:
    """汇总 soak 证据；<5 个不同完成交易日 → INSUFFICIENT。"""
    soak_dir = Path(soak_dir)
    days: list[str] = []
    if soak_dir.is_dir():
        for f in sorted(soak_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("manifest", {}).get("status") == "COMPLETE":
                    days.append(data["trade_date"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
    days = sorted(set(days))
    ready = len(days) >= MIN_SOAK_DAYS
    return {
        "gate": "O-12",
        "completed_trade_days": days,
        "count": len(days),
        "required": MIN_SOAK_DAYS,
        "status": "PASS" if ready else "INSUFFICIENT",
        "note": "" if ready else f"不足 {MIN_SOAK_DAYS} 个不同完成交易日（不伪造等待结果）",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P8 soak 证据收集与 O-12 判定")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--soak-dir", default="runtime/v2/soak")
    parser.add_argument("--collect", default=None, help="收集指定交易日证据（默认仅汇总）")
    args = parser.parse_args(argv)

    soak_dir = Path(args.soak_dir)
    if args.collect:
        soak_dir.mkdir(parents=True, exist_ok=True)
        evidence = collect_day_evidence(args.db, args.collect)
        path = soak_dir / f"{args.collect}.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"collected: {path}")
    status = soak_status(soak_dir)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
