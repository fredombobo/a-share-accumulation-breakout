"""P3.1 trial 账本测试：完整历史、状态统计、参数覆盖、产物防篡改。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.research_artifact_repository import (
    ArtifactError,
    artifacts_for_trial,
    register_artifact,
    sha256_of_file,
    verify_artifact,
)
from ab_screener.research.registry import register_experiment, register_trial
from ab_screener.research.trial_ledger import (
    parameter_space_coverage,
    status_counts,
    trial_history,
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "tl.db"))
    apply_pending(c)
    yield c
    c.close()


def _seed(conn) -> str:
    eid = register_experiment(conn, strategy="A", params={"vol_ratio": 1.5},
                              config_hash="cfg1")
    register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.5},
                   status="COMPLETED", outcome={"net_pf": 1.2})
    register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.6},
                   status="FAILED", outcome={"reason": "no data"})
    register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.5},
                   status="COMPLETED", outcome={"net_pf": 1.3})  # 同一参数重跑
    return eid


def test_trial_history_includes_failures(conn):
    eid = _seed(conn)
    history = trial_history(conn, eid)
    assert len(history) == 3
    statuses = {t["status"] for t in history}
    assert "FAILED" in statuses and "COMPLETED" in statuses
    counts = status_counts(conn, eid)
    assert counts["COMPLETED"] == 2 and counts["FAILED"] == 1
    assert counts["CANCELLED"] == 0 and counts["REJECTED"] == 0


def test_parameter_space_coverage(conn):
    eid = _seed(conn)
    cov = parameter_space_coverage(conn, eid)
    assert cov["trials"] == 3
    assert cov["distinct_params"] == 2   # {1.5, 1.6}
    assert cov["failed_or_rejected"] == 1


def test_trial_core_fields_append_only(conn):
    eid = _seed(conn)
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE research_trials SET params_json='{}' WHERE experiment_id=?", (eid,))
    conn.rollback()


def test_artifact_registration_and_tamper(conn, tmp_path: Path):
    eid = _seed(conn)
    tid = trial_history(conn, eid)[0]["trial_id"]
    f = tmp_path / "report.json"
    f.write_text('{"net_pf": 1.2}', encoding="utf-8")
    aid = register_artifact(conn, trial_id=tid, artifact_type="report", path=f)
    rows = artifacts_for_trial(conn, tid)
    assert len(rows) == 1 and rows[0]["artifact_id"] == aid
    # 内容未变 → 验证通过
    assert verify_artifact(f, rows[0]["content_sha256"]) is True
    # 篡改 → 验证失败
    f.write_text('{"net_pf": 9.9}', encoding="utf-8")
    assert verify_artifact(f, rows[0]["content_sha256"]) is False
    assert sha256_of_file(f) != rows[0]["content_sha256"]


def test_artifact_requires_existing_file(conn, tmp_path: Path):
    eid = _seed(conn)
    tid = trial_history(conn, eid)[0]["trial_id"]
    with pytest.raises(ArtifactError, match="不存在"):
        register_artifact(conn, trial_id=tid, artifact_type="report",
                          path=tmp_path / "nope.json")
