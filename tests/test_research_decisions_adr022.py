"""ADR-022：可用时点口径 rule-v1、OOS 封存、预登记文档 → registration、G0 审计。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ab_screener.data.g0_audit import run_g0_audit
from ab_screener.research import availability_policy as ap
from ab_screener.research.oos_seal import OosSealError, assert_window_allowed, unseal
from ab_screener.research.scorecard import build_registration, evaluate

TZ = ZoneInfo("Asia/Shanghai")
REPO = Path(__file__).resolve().parents[1]
HYPS = ("H-20260925-max-lottery", "H-20260925-abnormal-turnover", "H-20260925-ep-value")


# ── rule-v1 ──

def test_market_data_available_at_1800_and_usable_next_open() -> None:
    at = ap.rule_available_at("daily", trade_date="20240105")
    assert at == datetime(2024, 1, 5, 18, 0, tzinfo=TZ)
    assert ap.usable(at, ap.decision_at("20240108"))
    assert not ap.usable(at, datetime(2024, 1, 5, 14, 0, tzinfo=TZ))


def test_financials_require_first_disclosure_and_announcement_date() -> None:
    at = ap.rule_available_at("fina_indicator", f_ann_date="20240429", ann_date="20240427", first_disclosure=True)
    assert at == datetime(2024, 4, 29, 23, 59, tzinfo=TZ)
    with pytest.raises(ap.AvailabilityPolicyError, match="首次披露"):
        ap.rule_available_at("fina_indicator", f_ann_date="20240429", first_disclosure=False)
    with pytest.raises(ap.AvailabilityPolicyError, match="f_ann_date"):
        ap.rule_available_at("income", first_disclosure=True)


def test_chips_are_captured_only() -> None:
    with pytest.raises(ap.AvailabilityPolicyError, match="CAPTURED"):
        ap.rule_available_at("cyq", trade_date="20240105")
    assert ap.classify("cyq", datetime(2026, 9, 25, 18, 30, tzinfo=TZ)) == "CAPTURED"
    with pytest.raises(ap.AvailabilityPolicyError):
        ap.classify("cyq", None)
    assert ap.classify("daily", None) == "RULE_DERIVED"


def test_unknown_dataset_requires_adr_revision() -> None:
    with pytest.raises(ap.AvailabilityPolicyError, match="ADR-022"):
        ap.rule_available_at("margin", trade_date="20240105")


# ── OOS 封存 ──

def test_committed_seal_covers_all_three_hypotheses() -> None:
    seal = json.loads((REPO / "configs/research/oos_seal.json").read_text(encoding="utf-8"))["seals"][0]
    assert set(seal["hypotheses"]) == set(HYPS)
    assert (seal["sealed_from"], seal["sealed_to"]) == ("20240101", "20260925")


def test_in_sample_window_allowed_and_sealed_window_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert_window_allowed(HYPS[0], "20160101", "20231231", ledger_path=ledger)
    for window in (("20230601", "20240131"), ("20250101", "20250630"), ("20260901", "20261231")):
        with pytest.raises(OosSealError, match="封存"):
            assert_window_allowed(HYPS[0], *window, ledger_path=ledger)
    assert_window_allowed("H-unrelated", "20250101", "20250630", ledger_path=ledger)
    assert_window_allowed(HYPS[0], "20261001", "20261231", ledger_path=ledger)  # 封存之后的前向期


def test_unseal_is_one_time_and_per_hypothesis(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(OosSealError, match="SHA-256"):
        unseal(HYPS[0], registration_sha256="short", reason="G2 pass", ledger_path=ledger)
    record = unseal(HYPS[0], registration_sha256="a" * 64, reason="G2 PASS sha=...", ledger_path=ledger)
    assert record["hypothesis_id"] == HYPS[0]
    assert_window_allowed(HYPS[0], "20240101", "20260925", ledger_path=ledger)
    with pytest.raises(OosSealError, match="只能看一次"):
        unseal(HYPS[0], registration_sha256="a" * 64, reason="again", ledger_path=ledger)
    with pytest.raises(OosSealError, match="封存"):
        assert_window_allowed(HYPS[1], "20240101", "20240131", ledger_path=ledger)


# ── 预登记文档 ──

@pytest.mark.parametrize("hyp", HYPS)
def test_committed_preregistrations_register_and_pass_g1(hyp: str, tmp_path: Path) -> None:
    reg = build_registration(Path(f"docs/prereg/{hyp}.md"))
    assert reg["hypothesis_id"] == hyp
    assert reg["max_trials"] == 3
    assert reg["frozen_at"] == "2026-09-25T20:45:00+08:00"
    folder = tmp_path / hyp
    folder.mkdir()
    (folder / "registration.json").write_text(json.dumps(reg), encoding="utf-8")
    result = evaluate(tmp_path)
    gates = {g["gate"]: g["status"] for g in result["hypotheses"][0]["gates"]}
    assert gates["G1"] == "PASS"
    assert result["score"] == 2


def test_modified_common_protocol_blocks_registration(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs/prereg").mkdir(parents=True)
    for name in ("COMMON-CROSS-SECTION-PROTOCOL-V1.md", f"{HYPS[0]}.md"):
        (repo / "docs/prereg" / name).write_bytes((REPO / "docs/prereg" / name).read_bytes())
    build_registration(Path(f"docs/prereg/{HYPS[0]}.md"), repo_root=repo)
    common = repo / "docs/prereg/COMMON-CROSS-SECTION-PROTOCOL-V1.md"
    common.write_text(common.read_text(encoding="utf-8") + "\n事后放宽阈值\n", encoding="utf-8")
    with pytest.raises(ValueError, match="通用协议"):
        build_registration(Path(f"docs/prereg/{HYPS[0]}.md"), repo_root=repo)


def test_frozen_files_are_protected_from_eol_conversion() -> None:
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "docs/prereg/** -text" in attrs
    assert "configs/research/oos_seal.json -text" in attrs


# ── G0 审计 ──

def _audit_db(path: Path, *, partitions_ok: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE daily (ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
          pre_close REAL, change REAL, pct_chg REAL, vol REAL, amount REAL, PRIMARY KEY (ts_code, trade_date));
        CREATE TABLE delisted_basic (ts_code TEXT PRIMARY KEY, name TEXT, list_date TEXT, delist_date TEXT, updated_at TEXT);
        CREATE TABLE dataset_partitions (dataset TEXT, trade_date TEXT, row_count INTEGER, content_sha256 TEXT,
          revision INTEGER, ingested_at TEXT, PRIMARY KEY (dataset, trade_date));
        """
    )
    days = ["20240102", "20240103", "20240104"]
    for code in ("000001.SZ", "600000.SH", "000003.SZ"):
        for d in days:
            conn.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", (code, d, 10, 11, 9, 10.5, 10, 0.5, 5.0, 100, 1000))
    conn.execute("INSERT INTO delisted_basic VALUES ('000003.SZ','x','19900101','20240120','')")
    for d in days:
        rows = conn.execute(
            "SELECT ts_code, open, high, low, close, vol FROM daily WHERE trade_date=? ORDER BY ts_code", (d,)
        ).fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}\n".encode())
        conn.execute("INSERT INTO dataset_partitions VALUES ('daily',?,?,?,1,'')",
                     (d, len(rows), h.hexdigest() if partitions_ok else "bad"))
    conn.commit()
    conn.close()


def test_g0_audit_passes_measurable_items_but_not_assumed_costs(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _audit_db(db)
    root = tmp_path / "evidence"
    evidence = run_g0_audit(db, root, start="20240101", end="20240131")
    m = evidence["metrics"]
    assert m["pit_history_complete"] and m["delisted_covered"] and m["suspension_limit_modeled"]
    assert m["adjustment_complete"] and m["manifest_reproducible"]
    assert m["cost_model_calibrated"] is False  # 入库配置是假设值：必须用真实费率才能过
    result = evaluate(root)
    assert result["system"]["G0"]["status"] == "FAIL"
    assert result["score"] == 2
    assert any("成本" in b for b in result["next_blockers"])


def test_g0_audit_with_broker_statement_reaches_four(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _audit_db(db)
    root = tmp_path / "evidence"
    root.mkdir()
    statement = root / "broker" / "fees.pdf"
    statement.parent.mkdir()
    statement.write_bytes(b"%PDF fee schedule")
    cfg = json.loads((REPO / "configs/research/cost_model_v1.json").read_text(encoding="utf-8"))
    cfg.update(calibration_source="broker_statement", statement_path="broker/fees.pdf",
               statement_sha256=hashlib.sha256(statement.read_bytes()).hexdigest())
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps(cfg), encoding="utf-8")
    run_g0_audit(db, root, start="20240101", end="20240131", cost_path=cost)
    assert evaluate(root)["score"] == 4


def test_g0_audit_detects_manifest_drift(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _audit_db(db, partitions_ok=False)
    evidence = run_g0_audit(db, tmp_path / "evidence", start="20240101", end="20240131")
    assert evidence["metrics"]["manifest_reproducible"] is False


def test_g0_audit_opens_database_read_only(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _audit_db(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    run_g0_audit(db, tmp_path / "evidence", start="20240101", end="20240131")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
