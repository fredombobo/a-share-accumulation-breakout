from __future__ import annotations

import sqlite3
from pathlib import Path

from ab_screener.intelligence.ai_analysis import local_evidence_review
from local_store import LocalStore


def test_local_ai_review_is_read_only_and_works_without_provider(tmp_path: Path) -> None:
    db = tmp_path / "review.db"
    LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO stock_basic(ts_code,name,industry) VALUES ('000001.SZ','平安银行','银行')"
        )
        for index in range(25):
            date = f"202601{index + 1:02d}"
            conn.execute(
                "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount,pct_chg) "
                "VALUES ('000001.SZ',?,?,?,?,?,?,?,?)",
                (date, 10 + index / 100, 10.2 + index / 100, 9.8 + index / 100,
                 10 + index / 100, 1000 + index * 10, 10000, 0.1),
            )

    with sqlite3.connect(db) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ai_insights'"
        ).fetchone()[0]
    review = local_evidence_review(db, "000001.SZ")
    with sqlite3.connect(db) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ai_insights'"
        ).fetchone()[0]

    assert review["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert review["boundary"]["read_only"] is True
    assert review["boundary"]["triggers_order"] is False
    assert before == after == 0
