from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from ab_screener.data.adapters.fundamental_statements import (
    FIELDS,
    META,
    canonical_hash,
    fetch_statement_probe,
    normalize_observation,
)
from ab_screener.research.fundamental_audit import (
    evaluate_probe,
    ledger_fingerprint,
    local_financial_inventory,
)
from ab_screener.research.fundamental_factors import (
    ACCRUALS,
    GP,
    SUE,
    FinancialObservation,
    _quarter,
    evaluate_factor,
    factor_catalog,
    period_date,
)
from scripts import audit_fundamental_factors as cli
from scripts.audit_fundamental_factors import write_new_json

CODE = "600519.SH"
AT = "2025-06-01T15:00:00+08:00"


def observation(dataset, values, period="20241231", **overrides):
    raw = dict(ts_code=CODE, ann_date="20250301", f_ann_date="20250301", end_date=period,
               report_type="1", comp_type="1", update_flag="1", **values)
    row = normalize_observation(dataset, raw, ingested_at="2025-03-02T16:00:00+08:00")
    return replace(row, **overrides)


def gp_rows():
    return [observation("income", {"revenue": "120", "oper_cost": "60"}),
            observation("balancesheet", {"total_assets": "100"})]


def evaluate(rows, factor=GP, at=AT):
    return evaluate_factor(rows, ts_code=CODE, factor_id=factor, decision_at=at)


def test_gp_uses_gross_profit_to_assets_not_margin():
    result = evaluate(gp_rows())
    assert Decimal(result.value) == Decimal("0.6")
    assert result.status == "COMPUTABLE" and not result.candidate_eligible
    assert len(result.evidence) == 2


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", True, "bad"])
def test_missing_invalid_amounts_are_not_zero(value):
    rows = gp_rows()
    rows[0] = observation("income", {"revenue": value, "oper_cost": 60})
    assert evaluate(rows).code == "MISSING_FIELD"
    assert evaluate(rows).value is None


@pytest.mark.parametrize("assets", [0, -1])
def test_invalid_assets_fail(assets):
    assert evaluate([gp_rows()[0], observation("balancesheet", {"total_assets": assets})]).code == "INVALID_ASSETS"


def test_low_accruals_sign_and_all_balance_components():
    current = {"total_assets": 110, "total_cur_assets": 70, "money_cap": 12,
               "total_cur_liab": 40, "st_borr": 7, "non_cur_liab_due_1y": 4, "taxes_payable": 3}
    prior = {"total_assets": 90, "total_cur_assets": 50, "money_cap": 10,
             "total_cur_liab": 30, "st_borr": 5, "non_cur_liab_due_1y": 3, "taxes_payable": 2}
    rows = [observation("balancesheet", current), observation("balancesheet", prior, "20231231"),
            observation("cashflow", {"depr_fa_coga_dpba": 5, "amort_intang_assets": 1})]
    assert Decimal(evaluate(rows, ACCRUALS).value) == Decimal("-0.06")
    rows[1] = observation("balancesheet", {**prior, "taxes_payable": None}, "20231231")
    assert evaluate(rows, ACCRUALS).code == "MISSING_FIELD"
    rows[1] = observation("balancesheet", prior, "20221231")
    assert evaluate(rows, ACCRUALS).code == "MISSING_REPORT"


def test_future_ingestion_and_announcement_never_backdated():
    rows = gp_rows()
    assert evaluate(rows, at="2024-12-31T15:00:00+08:00").code == "NO_PIT_OBSERVATIONS"
    assert evaluate(rows, at="2025-03-02T15:00:00+08:00").code == "NO_PIT_OBSERVATIONS"
    raw = {"ts_code": CODE, "ann_date": "20250301", "f_ann_date": "20260601", "end_date": "20241231",
           "report_type": "1", "comp_type": "1", "update_flag": "1", "revenue": 120, "oper_cost": 60}
    future = normalize_observation("income", raw, ingested_at="2025-03-02T16:00:00+08:00")
    assert future.available_at == "2026-06-02T00:00:00+08:00"


def test_new_revision_cannot_change_old_asof_and_conflict_is_rejected():
    rows = gp_rows()
    future = observation("income", {"revenue": 1000, "oper_cost": 60},
                         ingested_at="2025-07-01T16:00:00+08:00", available_at="2025-07-01T16:00:00+08:00")
    assert evaluate([*rows, future]) == evaluate(rows)
    assert Decimal(evaluate([*rows, future], at="2025-07-02T15:00:00+08:00").value) == Decimal("9.4")
    conflict = replace(future, ingested_at=rows[0].ingested_at, available_at=rows[0].available_at)
    assert evaluate([*rows, conflict]).code == "AMBIGUOUS_REVISION"


def test_same_batch_distinct_publication_dates_select_latest_without_backdating():
    rows = gp_rows()
    original = replace(rows[0], available_at="2025-05-01T16:00:00+08:00", ingested_at="2025-05-01T16:00:00+08:00")
    revised = observation("income", {"revenue": 160, "oper_cost": 60},
                          f_ann_date="20250430", available_at=original.available_at,
                          ingested_at=original.ingested_at)
    assert Decimal(evaluate([original, revised, rows[1]]).value) == Decimal(1)
    assert Decimal(evaluate([revised, original, rows[1]]).value) == Decimal(1)
    assert evaluate([original, revised, rows[1]], at="2025-04-30T15:00:00+08:00").code == "MISSING_REPORT"
    fetched_old_later = replace(original, available_at="2025-05-10T16:00:00+08:00", ingested_at="2025-05-10T16:00:00+08:00")
    assert Decimal(evaluate([revised, fetched_old_later, rows[1]]).value) == Decimal(1)


@pytest.mark.parametrize("mutate,expected", [
    ({"comp_type": "2"}, "NON_INDUSTRIAL_COMPANY"),
    ({"source": ""}, "INVALID_LINEAGE"),
    ({"available_at": "2025-03-02T15:00:00+08:00"}, "INVALID_LINEAGE"),
    ({"effective_at": "2025-01-01T00:00:00+08:00"}, "INVALID_LINEAGE"),
    ({"available_at": "2025-03-02T16:00:00"}, "TIMEZONE_REQUIRED"),
])
def test_lineage_and_company_checks(mutate, expected):
    rows = gp_rows()
    rows[0] = replace(rows[0], **mutate)
    assert evaluate(rows).code == expected


def test_stale_and_unknown_factor_and_mixed_reporting():
    assert evaluate(gp_rows(), at="2026-07-10T15:00:00+08:00").code == "STALE_REPORT"
    assert evaluate(gp_rows(), "unknown").code == "UNKNOWN_FACTOR"
    assert evaluate([replace(gp_rows()[0], report_type="2"), gp_rows()[1]]).code == "MISSING_REPORT"


def eps_rows():
    rows = []
    # Numerical fixtures only; no synthetic investment results are reported.
    for lag in range(21):
        period = _quarter("20241231", lag)
        rows.append(observation("income", {"basic_eps": str(Decimal(30 - lag) ** 3 / 100)}, period,
                                report_type="2", eps_basis_evidence="fixture-original-documents-sha256"))
    return rows


def test_sue_uses_lagged_residual_volatility_not_current_growth_volatility():
    result = evaluate(eps_rows(), SUE)
    eps = [(30 - i) ** 3 / 100 for i in range(21)]
    growth = [eps[i] - eps[i + 4] for i in range(17)]
    residual = [growth[i] - sum(growth[i + 1:i + 9]) / 8 for i in range(9)]
    import statistics
    expected = residual[0] / statistics.stdev(residual[1:])
    assert float(result.value) == pytest.approx(expected)
    assert len(result.evidence) == 21


def test_sue_missing_quarter_basis_and_variance_fail():
    rows = eps_rows()
    assert evaluate(rows[:-1], SUE).code == "MISSING_REPORT"
    assert evaluate([replace(r, eps_basis_evidence=None) for r in rows], SUE).code == "EPS_BASIS_UNVERIFIED"
    assert evaluate([replace(r, values={"basic_eps": Decimal(1)}) for r in rows], SUE).code == "ZERO_VARIANCE"
    rows[0] = replace(rows[0], eps_basis_evidence="different-share-basis")
    assert evaluate(rows, SUE).code == "EPS_BASIS_UNVERIFIED"


class Provider:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.calls = []

    def query(self, dataset, **kwargs):
        self.calls.append((dataset, kwargs))
        if self.mode == "error":
            raise RuntimeError("TUSHARE_TOKEN=do-not-print-this-secret")
        raw = {k: "1" for k in (*META, *FIELDS[dataset])}
        raw.update(ts_code=CODE, ann_date="20250301", f_ann_date="20250301", end_date="20241231",
                   report_type=kwargs["report_type"])
        if self.mode == "wrong_code":
            raw["ts_code"] = "000001.SZ"
        if self.mode == "missing_columns":
            raw.pop("f_ann_date")
        if self.mode == "empty":
            return pd.DataFrame()
        return pd.DataFrame([raw])


def probe(provider, **kwargs):
    return fetch_statement_probe([CODE], start="20170101", end="20260905", provider=provider,
                                 clock=lambda: "2026-09-05T20:00:00+08:00", pause=lambda _: None, **kwargs)


def test_probe_uses_explicit_fields_types_and_ingestion_floor():
    provider = Provider()
    result = probe(provider)
    assert result["status"] == "COMPLETE" and len(result["observations"]) == 4
    assert len(provider.calls) == 4
    assert all(r["available_at"] == "2026-09-05T20:00:00+08:00" for r in result["observations"])
    assert all(r["eps_basis_evidence"] is None for r in result["observations"])
    assert not result["historical_vintages_verified"]
    assert all(call[1]["offset"] == 0 and call[1]["limit"] == 200 for call in provider.calls)
    report = evaluate_probe(result, decision_times=[AT])
    assert all(r["code"] == "NO_PIT_OBSERVATIONS" for r in report["checks"])
    assert not report["can_run_historical_experiment"]
    assert len(report["field_coverage"]) == 4
    # Serialization does not change identity.
    restored = json.loads(json.dumps(result, default=str))
    assert evaluate_probe(restored, decision_times=[AT]) == report
    restored["observations"][0]["values"]["revenue"] = "999999"
    with pytest.raises(ValueError, match="哈希"):
        evaluate_probe(restored, decision_times=[AT])


@pytest.mark.parametrize("mode,code", [("error", "PROVIDER_REQUEST_FAILED"), ("empty", "EMPTY_DATASET"),
                                      ("wrong_code", "REQUEST_NOT_HONORED"), ("missing_columns", "MISSING_COLUMNS")])
def test_probe_failures_do_not_silently_pass(mode, code):
    result = probe(Provider(mode))
    assert result["status"] == "INCOMPLETE" and not result["observations"]
    assert {r["code"] for r in result["issues"]} == {code}
    assert "do-not-print-this-secret" not in json.dumps(result)


def test_repeated_page_and_page_budget_fail_closed():
    result = probe(Provider(), page_size=1)
    assert {r["code"] for r in result["issues"]} == {"REPEATED_PAGE"}
    assert not result["observations"]
    result = probe(Provider(), page_size=1, max_pages=1)
    assert {r["code"] for r in result["issues"]} == {"PAGE_BUDGET_EXHAUSTED"}


def test_probe_refuses_unbounded_codes_before_network():
    provider = Provider()
    with pytest.raises(ValueError):
        fetch_statement_probe([CODE] * 11, start="20170101", end="20260905", provider=provider)
    assert not provider.calls


def test_read_only_audit_and_immutable_output(tmp_path):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE pt_account(id INTEGER, cash INTEGER)")
        conn.execute("INSERT INTO pt_account VALUES (1,100000)")
    before = ledger_fingerprint(db)
    report = local_financial_inventory(db)
    assert not report["tables"]["income"]["exists"]
    assert ledger_fingerprint(db) == before
    path = tmp_path / "report.json"
    digest = write_new_json(path, report)
    import hashlib
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_new_json(path, {"status": "PASS"})
    assert ledger_fingerprint(db) == before


def test_catalog_is_fixed_research_only_and_not_engine_replacement():
    catalog = factor_catalog()
    assert [r["id"] for r in catalog] == [GP, ACCRUALS, SUE]
    assert all(not r["production_ready"] and not r["candidate_eligible"] for r in catalog)
    assert all("8db892442" in r["source_url"] for r in catalog)
    assert period_date("20241231").isoformat() == "2024-12-31T00:00:00+08:00"
    assert canonical_hash({"x": Decimal("1.5")}) == canonical_hash({"x": "1.5"})


def test_observation_type_has_required_pit_metadata():
    assert {"effective_at", "available_at", "ingested_at", "source", "revision"} <= set(FinancialObservation.__dataclass_fields__)


def cli_root(tmp_path, monkeypatch):
    root = tmp_path / "accumulation_breakout"
    (root / "runtime").mkdir(parents=True)
    with sqlite3.connect(root / "runtime/stock_data.db") as conn:
        conn.execute("CREATE TABLE pt_account(id INTEGER, cash INTEGER)")
        conn.execute("INSERT INTO pt_account VALUES(1,12345)")
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(cli, "build_version", lambda: "offline-fixture")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    return root


def test_cli_local_check_exits_nonzero_without_network(tmp_path, monkeypatch):
    root = cli_root(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "fetch_statement_probe", lambda *a, **k: pytest.fail("unexpected network"))
    output = root / "runtime/research/local"
    monkeypatch.setattr("sys.argv", ["audit", "--output", str(output)])
    assert cli.main() == 2
    result = json.loads((output / "qualification.json").read_text(encoding="utf-8"))
    assert result["status"] == "NOT_RUN" and result["ledger_unchanged"]
    assert not result["return_experiment_executed"]


def test_cli_replays_verified_snapshot_offline_and_rejects_tampering(tmp_path, monkeypatch):
    root = cli_root(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "CODES", [CODE])
    source = root / "runtime/research/source"
    source.mkdir(parents=True)
    data = probe(Provider())
    digest = write_new_json(source / "provider_probe.json", data)
    write_new_json(source / "manifest.json", {"files_sha256": {"provider_probe.json": digest}})
    monkeypatch.setattr(cli, "fetch_statement_probe", lambda *a, **k: pytest.fail("unexpected network"))
    output = root / "runtime/research/replay"
    monkeypatch.setattr("sys.argv", ["audit", "--replay", str(source / "provider_probe.json"), "--output", str(output)])
    assert cli.main() == 2
    result = json.loads((output / "qualification.json").read_text(encoding="utf-8"))
    assert result["status"] == "BLOCKED" and result["observation_count"] == 4
    # Fixture tampering only; a real report is never overwritten.
    (source / "provider_probe.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        cli.main()


def test_preregistration_frozen_hash_and_checkout_newlines(tmp_path, monkeypatch):
    assert cli.preregistration_hash() == cli.PREREG_SHA256
    frozen = cli.PREREG.read_text(encoding="utf-8")
    alternate = tmp_path / "prereg.md"
    alternate.write_bytes(frozen.replace("\n", "\r\n").encode())
    monkeypatch.setattr(cli, "PREREG", alternate)
    assert cli.preregistration_hash() == cli.PREREG_SHA256
