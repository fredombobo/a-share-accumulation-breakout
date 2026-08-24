"""soak 计数规则（§5/§7）：只计不同真实 COMPLETE 交易日；DAG FAILED、
manifest 缺失/PARTIAL、日期伪造、仅 pt_cycle 均不计数；重复收集不增天。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from scripts.soak_monitor_v2 import (
    collect_day_evidence,
    main,
    soak_status,
)

pytestmark = pytest.mark.fault_injection


def _seed(db: str, trade_date: str, *, dag_status: str = "COMPLETED",
          manifest_status: str = "COMPLETE", manifest_sha: str | None = None,
          pt_cycle_only: bool = False) -> None:
    manifest_sha = manifest_sha or f"sha256-{trade_date}"
    with sqlite3.connect(db) as c:
        if pt_cycle_only:
            c.execute(
                "INSERT OR REPLACE INTO pt_cycle (cycle_id, run_date, phase, retry_count,"
                " started_at, finished_at) VALUES (?,?,'DONE',0,?,?)",
                (f"CY-{trade_date}", trade_date, "t", "t"),
            )
        else:
            c.execute(
                "INSERT INTO dag_runs (run_id, trade_date, mode, status, created_at,"
                " finished_at) VALUES (?,?,'EOD',?,?,?)",
                (f"R-{trade_date}", trade_date, dag_status, "t", "t"),
            )
            c.execute(
                "INSERT INTO daily_run_manifests (manifest_id, trade_date, account_id,"
                " status, code_version, config_hash, payload_json, blockers_json,"
                " manifest_sha256, created_at) VALUES (?,?,1,?,'git-x','cfg-x',?,?,?,?)",
                (f"DM-{trade_date}", trade_date, manifest_status, "{}", "[]",
                 manifest_sha, "t"),
            )
        c.commit()


@pytest.fixture()
def db(tmp_path: Path) -> str:
    from ab_screener.data.migrations_v2 import run_v2_migrations

    db = str(tmp_path / "soak.db")
    with sqlite3.connect(db) as c:
        apply_pending(c)
        c.commit()
    run_v2_migrations(db)  # daily_run_manifests 等 numbered migrations（v9–v13）
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS pt_cycle (cycle_id TEXT PRIMARY KEY,"
            " run_date TEXT NOT NULL, phase TEXT NOT NULL, retry_count INTEGER DEFAULT 0,"
            " started_at TEXT, finished_at TEXT)"
        )
        c.commit()
    return db


def _write_evidence(soak_dir: Path, trade_date: str, db: str,
                    **overrides) -> Path:
    from scripts.soak_monitor_v2 import _write_evidence as _write

    evidence = collect_day_evidence(db, trade_date, code_version="git-x",
                                    config_hash="cfg-x")
    for key, value in overrides.items():
        evidence[key] = value
    return _write(Path(soak_dir), evidence)


def test_completed_day_counts(db, tmp_path):
    _seed(db, "20260818")
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    status = soak_status(soak)
    assert status["completed_trade_days"] == ["20260818"]
    assert status["count"] == 1


def test_dag_failed_not_counted(db, tmp_path):
    _seed(db, "20260818", dag_status="FAILED")
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    status = soak_status(soak)
    assert status["count"] == 0
    assert status["status"] == "INSUFFICIENT"


def test_manifest_missing_not_counted(db, tmp_path):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO dag_runs (run_id, trade_date, mode, status, created_at,"
            " finished_at) VALUES ('R-1','20260818','EOD','COMPLETED','t','t')"
        )
        c.commit()
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    status = soak_status(soak)
    assert status["count"] == 0


def test_manifest_partial_not_counted(db, tmp_path):
    _seed(db, "20260818", manifest_status="PARTIAL")
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    assert soak_status(soak)["count"] == 0


def test_pt_cycle_only_not_counted(db, tmp_path):
    _seed(db, "20260818", pt_cycle_only=True)
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    assert soak_status(soak)["count"] == 0


def test_fake_date_not_counted(db, tmp_path):
    """日期伪造不计数：收集阶段拒绝非法交易日；手工伪造的证据文件也不计天。"""
    _seed(db, "20261399")
    soak = tmp_path / "soak"
    # 收集阶段 fail-closed：非合法 YYYYMMDD 直接拒绝（ValueError）
    from scripts.soak_monitor_v2 import collect_day_evidence

    with pytest.raises(ValueError, match="YYYYMMDD"):
        collect_day_evidence(db, "20261399")
    # 即使手工伪造证据文件（含 DAG COMPLETED + manifest COMPLETE），计数仍为 0
    soak.mkdir(parents=True, exist_ok=True)
    (soak / "20261399.json").write_text(json.dumps({
        "schema": "soak-evidence-v2", "trade_date": "20261399",
        "collected_at": "t", "identity": {},
        "dag": {"run_id": "R-fake", "status": "COMPLETED", "finished_at": "t"},
        "manifest": {"status": "COMPLETE", "sha256": "sha-fake", "created_at": "t"},
    }, ensure_ascii=False), encoding="utf-8")
    status = soak_status(soak)
    assert status["count"] == 0
    assert status["status"] == "INSUFFICIENT"


def test_repeated_collection_same_day_not_incremented(db, tmp_path):
    _seed(db, "20260818")
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    _write_evidence(soak, "20260818", db)
    status = soak_status(soak)
    assert status["count"] == 1


def test_tampered_evidence_not_counted(db, tmp_path):
    _seed(db, "20260818")
    soak = tmp_path / "soak"
    path = _write_evidence(soak, "20260818", db)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dag"]["status"] = "FAILED"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    status = soak_status(soak)
    assert status["count"] == 0
    assert "20260818.json" in status["invalid_evidence_files"]


def test_insufficient_days_exit_nonzero(db, tmp_path):
    _seed(db, "20260818")
    soak = tmp_path / "soak"
    _write_evidence(soak, "20260818", db)
    code = main(["--db", db, "--soak-dir", str(soak),
                 "--code-version", "git-x", "--config-hash", "cfg-x"])
    assert code == 1  # <5 天 → 退出码非零
    status = soak_status(soak)
    assert status["status"] == "INSUFFICIENT"
    assert "不足" in status["note"]


def test_evidence_includes_identity_and_chain(db, tmp_path):
    """证据含交易日/收集时点/身份/DAG run/manifest hash/对账/审计 head/自哈希。"""
    from ab_screener.application.audit_service import record_audit_event

    _seed(db, "20260818")
    with sqlite3.connect(db) as c:
        record_audit_event(c, actor="scheduler", action="DAG_RUN_FINISHED",
                           request={"trade_date": "20260818"}, correlation_id="R-20260818")
    soak = tmp_path / "soak"
    path = _write_evidence(soak, "20260818", db)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trade_date"] == "20260818"
    assert data["collected_at"]
    assert data["identity"]["code_version"] == "git-x"
    assert data["identity"]["config_hash"] == "cfg-x"
    assert data["dag"]["status"] == "COMPLETED"
    assert data["manifest"]["status"] == "COMPLETE"
    assert data["manifest"]["sha256"] == "sha256-20260818"
    assert data["audit_chain_head"]["event_hash"]
    assert data["file_self_sha256"]
    # 自身 SHA-256 自校验通过（已含在 evidence 中）
    from scripts.soak_monitor_v2 import _read_evidence

    assert _read_evidence(path) is not None


def test_five_distinct_days_pass(db, tmp_path):
    soak = tmp_path / "soak"
    for day in ("20260810", "20260811", "20260812", "20260813", "20260814"):
        _seed(db, day)
        _write_evidence(soak, day, db)
    status = soak_status(soak)
    assert status["count"] == 5
    assert status["status"] == "PASS"
    code = main(["--db", db, "--soak-dir", str(soak)])
    assert code == 0
