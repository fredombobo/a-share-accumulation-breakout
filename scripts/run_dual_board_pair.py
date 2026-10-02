"""Frozen 50+50 mature dual-board experiment; no threshold or universe search."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.research.fundamental_audit import ledger_fingerprint
from ab_screener.research.intermediate_momentum import MECHANISM_ID
from ab_screener.research.pit_reader import build_research_pit_snapshot
from ab_screener.research.professional_grid import request_hash
from ab_screener.research.professional_runner import execute_professional_run, prepare_professional_request
from ab_screener.research.resilient_absorption import BASE_ENTRY_MECHANISM_ID, entry_mechanism_identity
from ab_screener.research.store import ResearchRunStore
from ab_screener.research.trusted_run import COST_VERSION
from build_version import build_version
from scripts.run_market_excess_pair import evaluate_pair

PROTOCOL = "dual-board-20260905-v1"
SOURCE = "probt-49462a73e38d"
PREREG = ROOT / "docs/DUAL-BOARD-PREREGISTRATION-2026-09-05.md"
OUTPUT = ROOT / f"runtime/research/{PROTOCOL}"
BOARDS = ("创业板", "科创板")


def select_dual_board(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Selection has no access to prices, returns, signals or report metrics."""
    eligible = []
    for row in rows:
        code, board, listed = str(row["ts_code"]), str(row["market"]), str(row["list_date"] or "")
        matches = ((board == "创业板" and code.startswith(("300", "301")) and code.endswith(".SZ"))
                   or (board == "科创板" and code.startswith("688") and code.endswith(".SH")))
        if not matches or len(listed) != 8 or not listed.isdigit() or listed > "20220201":
            continue
        datetime.strptime(listed, "%Y%m%d")
        eligible.append({"ts_code": code, "market": board, "list_date": listed})
    if len({row["ts_code"] for row in eligible}) != len(eligible):
        raise ValueError("股票池重复代码，停止")
    selected = []
    for board in BOARDS:
        pool = [row for row in eligible if row["market"] == board]
        if len(pool) < 50:
            raise ValueError(f"{board} 不足50只成熟股票，禁止换池")
        selected.extend(sorted(pool, key=lambda row: hashlib.sha256(
            f"{PROTOCOL}:{row['ts_code']}".encode()).hexdigest())[:50])
    codes = sorted(row["ts_code"] for row in selected)
    return {"codes": codes, "selected": sorted(selected, key=lambda row: row["ts_code"]),
            "population": sorted(eligible, key=lambda row: row["ts_code"]),
            "code_sha256": hashlib.sha256("\n".join(codes).encode()).hexdigest(),
            "counts": {board: 50 for board in BOARDS}, "seed": PROTOCOL,
            "selection_uses_returns": False, "historical_membership_verified": False}


class ExistingResearchStore(ResearchRunStore):
    """Use only an already-migrated production research schema; never migrate."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        if not {11, 12, 13}.issubset(versions):
            raise ValueError("研究表尚未迁移，本脚本不得在生产自动迁移")


def prepare(db: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        source = json.loads(conn.execute("SELECT result_json FROM research_runs WHERE research_run_id=?", (SOURCE,)).fetchone()[0])
        selection = select_dual_board([dict(row) for row in conn.execute(
            "SELECT ts_code,market,list_date FROM stock_basic ORDER BY ts_code")])
        lifecycle = conn.execute(
            "WITH ranked AS (SELECT ts_code,payload_json,ROW_NUMBER() OVER "
            "(PARTITION BY ts_code ORDER BY revision DESC,available_at DESC) AS rn "
            "FROM instrument_lifecycle_history WHERE available_at<=?) "
            "SELECT ts_code,payload_json FROM ranked WHERE rn=1", (source["snapshot"]["decision_at"],)
        ).fetchall()
    life = {row[0]: json.loads(row[1]) for row in lifecycle}
    for row in selection["selected"]:
        record = life.get(row["ts_code"], {})
        if (record.get("security_type") != "stock" or record.get("list_date") != row["list_date"]
                or record.get("delist_date")):
            raise ValueError(f"生命周期不匹配或含退市特殊状态: {row['ts_code']}")
    windows = source["request"]["windows"]
    payload = {"strategy": "A", "sample_step": 1, "max_codes": 100,
               "parameters": source["request"]["parameters"], "conditions": [],
               "universe": {"classification": "market", "groups": list(BOARDS), "codes": selection["codes"]},
               "windows": {"mode": "manual", "is_start": windows["is"][0], "is_end": windows["is"][1],
                           "oos_start": windows["oos"][0], "oos_end": windows["oos"][1]}}
    prepared = prepare_professional_request(db, payload)
    if not prepared["data_scope"]["can_run"]:
        raise ValueError(f"冻结样本数据不完整，禁止换股: {prepared['data_scope']['issues']}")
    prepared["windows"] = deepcopy(windows)
    prepared["knowledge_cutoff"] = source["snapshot"]["decision_at"]
    prepared["research_protocol"] = {"id": PROTOCOL, "source": SOURCE,
        "preregistration_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "holdout_already_observed": True, "style_benchmark_status": "MISSING_LOCAL_DATA",
        "board_counts": selection["counts"], "candidate_eligible": False}
    prepared["research_boundary"] = {"mode": "PREREGISTERED_HISTORICAL_DIAGNOSTIC",
        "candidate_eligible": False, "note": "双创各50只成熟股票探索；风格指数缺失，不能宣称跑赢双创，不自动启用选股。"}
    prepared["input_hash"] = request_hash(prepared)
    snapshot = build_research_pit_snapshot(db, study_start=windows["is"][0], study_end=windows["oos"][1],
        history_days=max(540, prepared["parameter_space"]["horizon"] * 2), max_codes=100,
        universe_codes=selection["codes"], benchmark_code="000300.SH", decision_at=prepared["knowledge_cutoff"])
    # Mature-cohort ordinary-session assumptions cannot explain prices outside
    # +/-20% (allow one cent rounding). Do not silently process exceptional days.
    d = snapshot.daily
    invalid = d[(d["vol"] > 0) & ((d["pre_close"] <= 0) | (d["pre_close"].isna())
        | (d["high"] > d["pre_close"] * 1.2 + 0.011) | (d["low"] < d["pre_close"] * .8 - 0.011))]
    if not invalid.empty:
        raise ValueError(f"双创普通交易约束外行情，先审查: {invalid[['ts_code', 'trade_date']].head().to_dict('records')}")
    evidence = {"selection": selection, "snapshot": snapshot.identity(), "ledger": ledger_fingerprint(db)}
    return prepared, evidence


def write_once(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError(f"禁止覆盖不同证据: {path.name}")
        return
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    db = ROOT / "runtime/stock_data.db"
    if ROOT.name != "accumulation_breakout":
        raise ValueError("仅允许 AB/8001")
    prepared, evidence = prepare(db)
    version = prepared["code_version"]
    print(json.dumps({"code_version": version, "counts": evidence["selection"]["counts"],
        "population": len(evidence["selection"]["population"]), "snapshot": evidence["snapshot"],
        "windows": prepared["windows"], "execute": args.execute}, ensure_ascii=False), flush=True)
    if not args.execute:
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_once(OUTPUT / "registration.json", {"prepared": prepared, "evidence": evidence})
    store = ExistingResearchStore(db)
    results = []
    for label, mechanism in [("control", BASE_ENTRY_MECHANISM_ID), ("momentum", MECHANISM_ID)]:
        request = deepcopy(prepared)
        request["entry_mechanism"] = entry_mechanism_identity(mechanism)
        request["input_hash"] = request_hash(request)
        run_id = f"probt-dual-v1-{label}"
        existing = store.get(run_id)
        if existing:
            if existing["input_hash"] != request["input_hash"] or existing["status"] != "done":
                raise ValueError(f"{run_id} 身份不同或未完成，禁止覆盖/自动重试")
            results.append(existing["result"])
            continue
        store.create_run(run_id, strategy="A", research_mode="professional_grid", request=request,
            input_hash=request["input_hash"], dataset_version=prepared["knowledge_cutoff"],
            code_version=version, cost_version=COST_VERSION, config_hash=prepared["parameter_space"]["sha256"])

        def progress(phase: str, pct: int, message: str, task_id: str = run_id) -> None:
            store.update(task_id, status="running", phase=phase, progress=pct, message=message,
                heartbeat_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
            print(f"{task_id} | {phase} {pct}% | {message}", flush=True)

        def cancelled(task_id: str = run_id) -> bool:
            return store.is_cancel_requested(task_id)

        try:
            result = execute_professional_run(db, request, progress=progress,
                cancel_check=cancelled)
            if build_version() != version or result.get("snapshot") != evidence["snapshot"]:
                raise ValueError("代码或数据身份在运行期间改变，停止")
            store.update(run_id, status="done", phase="DONE", progress=100, result=result,
                verdict=result["verdict"], message=result["verdict_label"], candidate_eligible=False,
                can_claim_edge=False, report_markdown=result["report_markdown"])
        except Exception as exc:
            from optimizer import ResearchCancelled
            from tushare_init import sanitize_error

            store.update(run_id, status="cancelled" if isinstance(exc, ResearchCancelled) else "error",
                phase="STOPPED", message=sanitize_error(exc)[:500], candidate_eligible=False, can_claim_edge=False)
            raise
        write_once(OUTPUT / f"{label}.json", result)
        results.append(result)
    summary = evaluate_pair(*results)
    summary.update(protocol=PROTOCOL, style_benchmark_status="MISSING_LOCAL_DATA",
        run_ids=["probt-dual-v1-control", "probt-dual-v1-momentum"],
        ledger_unchanged=ledger_fingerprint(db) == evidence["ledger"])
    write_once(OUTPUT / "comparison.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["ledger_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
