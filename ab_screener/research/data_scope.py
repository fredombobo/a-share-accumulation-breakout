"""Read-only, request-scoped provenance checks including indicator warmup.

This is a completeness check, NOT a claim that historical membership or every
timestamp was independently verified. The PIT reader still checks visible
revisions, hashes, prices and lifecycle evidence when freezing a run.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def inspect_scope(db_path: str | Path, codes: list[str], start: str, end: str,
                  history_days: int) -> dict[str, Any]:
    warmup = (datetime.strptime(start, "%Y%m%d") - timedelta(days=history_days)).strftime("%Y%m%d")
    issues: list[str] = []
    examples: list[dict[str, str]] = []
    missing_metadata = missing_history = rows_checked = 0
    history_count = 0
    latest_revision = ""
    last_incomplete_date = ""
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=60) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(daily)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        bounds = conn.execute("SELECT MIN(trade_date),MAX(trade_date) FROM daily").fetchone()
        required = {"effective_at", "available_at", "ingested_at", "source", "revision"}
        if not required <= columns:
            issues.append("行情表缺少来源/时点字段，需正规迁移与数据同步；不能猜测补填。")
        if not {"daily_history", "instrument_lifecycle_history"} <= tables:
            issues.append("缺少不可变行情或生命周期历史，不能冻结 PIT 研究。")
        if not issues:
            bad = " OR ".join(f"d.{key} IS NULL OR TRIM(CAST(d.{key} AS TEXT))=''" for key in sorted(required))
            for index in range(0, len(codes), 500):
                chunk = codes[index:index + 500]
                marks = ",".join("?" for _ in chunk)
                args = (*chunk, warmup, end)
                where = f"d.ts_code IN ({marks}) AND d.trade_date BETWEEN ? AND ?"
                checked, absent, last_bad = conn.execute(
                    f"SELECT COUNT(*),SUM(CASE WHEN {bad} THEN 1 ELSE 0 END),"
                    f"MAX(CASE WHEN {bad} THEN trade_date END) FROM daily d WHERE {where}", args,
                ).fetchone()
                last_incomplete_date = max(last_incomplete_date, str(last_bad or ""))
                rows_checked += int(checked)
                missing_metadata += int(absent or 0)
                missing, last_missing = conn.execute(
                    f"SELECT COUNT(*),MAX(trade_date) FROM daily d WHERE {where} AND NOT EXISTS"
                    " (SELECT 1 FROM daily_history h WHERE h.ts_code=d.ts_code AND h.trade_date=d.trade_date)", args,
                ).fetchone()
                last_incomplete_date = max(last_incomplete_date, str(last_missing or ""))
                missing_history += int(missing)
                count, latest = conn.execute(
                    f"SELECT COUNT(*),MAX(available_at) FROM daily_history d WHERE {where}", args,
                ).fetchone()
                history_count += int(count)
                latest_revision = max(latest_revision, str(latest or ""))
                if len(examples) < 5:
                    for code, day in conn.execute(
                        f"SELECT d.ts_code,d.trade_date FROM daily d WHERE {where} AND ({bad}) LIMIT 5", args,
                    ):
                        examples.append({"code": str(code), "date": str(day)})
            if not rows_checked:
                issues.append("所选股票和研究/预热范围没有行情。")
            if missing_metadata:
                issues.append(f"研究及预热范围有 {missing_metadata} 条行情缺来源/时点；请补充可验证数据或缩短研究范围。")
            if missing_history:
                issues.append(f"有 {missing_history} 条行情缺不可变历史版本，不能用于当前复验。")
    result: dict[str, Any] = {
        "version": "research-scope-v1", "can_run": not issues,
        "browse_start": bounds[0], "browse_end": bounds[1],
        "study_start": start, "study_end": end, "warmup_start": warmup,
        "history_calendar_days": history_days, "rows_checked": rows_checked,
        "missing_metadata": missing_metadata, "missing_history": missing_history,
        "last_incomplete_date": last_incomplete_date or None,
        "history_revision_count": history_count, "latest_history_available_at": latest_revision,
        "issues": issues, "examples": examples[:5],
        "note": "只检查本次研究及预热范围；可浏览历史不等于可验证历史。当前分类不是历史成员 PIT。运行时仍须验证行情哈希和生命周期。",
    }
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
