"""Frozen AB research batch with per-group evidence and no production migration."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.research.fundamental_audit import ledger_fingerprint
from ab_screener.research.pit_reader import build_research_pit_snapshot
from ab_screener.research.professional_grid import request_hash
from ab_screener.research.professional_runner import execute_professional_run, prepare_professional_request
from ab_screener.research.trusted_run import COST_VERSION, trusted_portfolio_identity
from build_version import build_version
from scripts.run_dual_board_pair import ExistingResearchStore, write_once

PROTOCOL = "batch-20260919-v2"
DB = ROOT / "runtime/stock_data.db"
OUTPUT = ROOT / "runtime/research" / PROTOCOL
PREREG = ROOT / "docs/RESEARCH-BATCH-2026-09-19.md"


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def source(run_id: str) -> dict[str, Any]:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT request_json,result_json FROM research_runs WHERE research_run_id=?",
                           (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Missing source: {run_id}")
    return {"request": json.loads(row[0]), "result": json.loads(row[1] or "{}"),
            "sha256": hashlib.sha256((row[0] + (row[1] or "")).encode()).hexdigest()}


def snapshot_identity(request: dict[str, Any]) -> dict[str, Any]:
    windows = request["windows"]
    starts = [windows["is"][0]] + [row["train_start"] for row in windows.get("wf", [])]
    ends = [windows["oos"][1]] + [row["test_end"] for row in windows.get("wf", [])]
    return build_research_pit_snapshot(DB, study_start=min(starts), study_end=max(ends),
        history_days=max(540, request["parameter_space"]["horizon"] * 2),
        max_codes=len(request["universe"]["codes"]), universe_codes=request["universe"]["codes"],
        benchmark_code="000300.SH", decision_at=request["knowledge_cutoff"]).identity()


def prepare() -> None:
    if (OUTPUT / "registration.json").exists():
        raise ValueError("Batch already registered; execute its frozen manifest instead of replacing it")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    version = build_version()
    prereg_hash = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    profit_id = "probt-e0a5962dc8aa"
    profit = source(profit_id)
    selected = profit["result"]["selected"]
    payload = {"strategy": "A", "sample_step": profit["request"]["sample_step"], "max_codes": 600,
        "universe": {"codes": profit["request"]["universe"]["codes"]},
        "parameters": {key: {"mode": "fixed", "value": value}
                       for key, value in {**selected["signal"], **selected["exit"]}.items()},
        "conditions": [], "windows": {"mode": "manual", "is_start": "20230801", "is_end": "20250731",
                                       "oos_start": "20250801", "oos_end": "20260731"}}
    historical = prepare_professional_request(DB, payload)
    write_once(OUTPUT / "historical-profit-qualification.json", {
        "source": profit_id, "source_sha256": profit["sha256"], "data_scope": historical["data_scope"],
        "status": "QUALIFIED" if historical["data_scope"]["can_run"] else "BLOCKED_DATA_LINEAGE"})
    payload["windows"] = {"mode": "auto"}
    refreshed = prepare_professional_request(DB, payload)
    items = [("profit-refresh", profit_id, refreshed, profit, "WINDOW_REFRESH_DIAGNOSTIC")]
    for label, run_id in [
        ("mom-control", "probt-mom6m-v1-control"), ("mom-momentum", "probt-mom6m-v1-momentum"),
        ("dual-control", "probt-dual-v1-control"), ("dual-momentum", "probt-dual-v1-momentum"),
        ("grid432", "probt-1acfad78443b"),
    ]:
        old = source(run_id)
        items.append((label, run_id, deepcopy(old["request"]), old, "FROZEN_CORRECTIVE_REPLAY"))
    arms = []
    for label, source_id, request, old, comparison_kind in items:
        if not request.get("data_scope", {}).get("can_run"):
            raise ValueError(f"Data scope not qualified: {label}")
        request["code_version"] = version
        request["portfolio_model"] = trusted_portfolio_identity()
        request["research_protocol"] = {"id": PROTOCOL, "source": source_id,
            "source_sha256": old["sha256"], "preregistration_sha256": prereg_hash,
            "holdout_already_observed": True, "comparison_kind": comparison_kind}
        if request["parameter_space"]["count"] == 1:
            request["research_boundary"] = {"mode": "PREREGISTERED_HISTORICAL_DIAGNOSTIC",
                "candidate_eligible": False, "note": "已观察历史诊断，保留失败和小样本，禁止晋级。"}
        request.pop("input_hash", None)
        request["input_hash"] = request_hash(request)
        identity = snapshot_identity(request)
        if (comparison_kind == "FROZEN_CORRECTIVE_REPLAY" and old["result"].get("snapshot")
                and identity != old["result"]["snapshot"]):
            raise ValueError(f"Historical snapshot changed: {label}")
        run_id = f"probt-20260919-v2-{label}"
        write_once(OUTPUT / f"{label}-request.json", request)
        arms.append({"label": label, "run_id": run_id, "source": source_id,
                     "input_hash": request["input_hash"], "snapshot": identity,
                     "combinations": request["parameter_space"]["count"]})
        print(json.dumps({"prepared": label, "codes": len(request["universe"]["codes"]),
            "combinations": request["parameter_space"]["count"], "windows": request["windows"],
            "snapshot": identity}, ensure_ascii=False), flush=True)
    if build_version() != version:
        raise ValueError("Source code changed during registration")
    write_once(OUTPUT / "registration.json", {"protocol": PROTOCOL, "created_at": now(),
        "code_version": version, "portfolio_model": trusted_portfolio_identity(),
        "preregistration_sha256": prereg_hash, "ledger_before": ledger_fingerprint(DB), "arms": arms,
        "max_seconds": 7200, "selection_basis": "IS_ONLY", "candidate_eligible": False})


def execute() -> int:
    from optimizer import ResearchCancelled
    from tushare_init import sanitize_error

    manifest = json.loads((OUTPUT / "registration.json").read_text(encoding="utf-8"))
    if (OUTPUT / "summary.json").exists():
        raise ValueError("Batch already finished; do not overwrite results")
    if hashlib.sha256(PREREG.read_bytes()).hexdigest() != manifest["preregistration_sha256"]:
        raise ValueError("Preregistration changed")
    if trusted_portfolio_identity() != manifest["portfolio_model"]:
        raise ValueError("Portfolio identity changed")
    store = ExistingResearchStore(DB)
    deadline = time.monotonic() + manifest["max_seconds"]
    summaries = []
    stop_batch = False
    for arm in manifest["arms"]:
        if time.monotonic() >= deadline or (OUTPUT / "STOP").exists():
            stop_batch = True
            break
        if build_version() != manifest["code_version"]:
            raise ValueError("Code changed; frozen batch cannot continue")
        label, run_id = arm["label"], arm["run_id"]
        request = json.loads((OUTPUT / f"{label}-request.json").read_text(encoding="utf-8"))
        unsigned = {key: value for key, value in request.items() if key != "input_hash"}
        if request_hash(unsigned) != arm["input_hash"] or request["input_hash"] != arm["input_hash"]:
            raise ValueError(f"Request changed: {label}")
        if store.get(run_id):
            raise ValueError(f"Run already exists: {run_id}; no automatic rerun or overwrite")
        evidence_dir = OUTPUT / label
        evidence_dir.mkdir(exist_ok=True)
        store.create_run(run_id, strategy="A", research_mode="professional_grid", request=request,
            input_hash=request["input_hash"], dataset_version=request["knowledge_cutoff"],
            code_version=manifest["code_version"], cost_version=COST_VERSION,
            config_hash=request["parameter_space"]["sha256"])
        started = time.monotonic()
        completed_groups = 0
        trial_count = 0

        def cancelled(task_id: str = run_id) -> bool:
            return ((OUTPUT / "STOP").exists() or time.monotonic() >= deadline
                    or store.is_cancel_requested(task_id))

        def progress(phase: str, pct: int, message: str, task_id: str = run_id) -> None:
            store.update(task_id, status="running", phase=phase, progress=pct,
                         message=message, heartbeat_at=now())
            print(f"{now()} | {task_id} | {phase} {pct}% | {message}", flush=True)

        def checkpoint(rows: list[dict[str, Any]], task_id: str = run_id, directory: Path = evidence_dir,
                       input_hash: str = request["input_hash"], snapshot: dict[str, Any] = arm["snapshot"]) -> None:
            nonlocal completed_groups, trial_count
            completed_groups += 1
            trial_count += len(rows)
            write_once(directory / f"group-{completed_groups:03d}.json", {
                "input_hash": input_hash, "snapshot": snapshot, "rows": rows})
            store.update(task_id, checkpoint={"completed_groups": completed_groups,
                "completed_combinations": trial_count, "evidence_directory": str(directory)})

        try:
            if snapshot_identity(request) != arm["snapshot"]:
                raise ValueError("Frozen data snapshot changed before execution")
            result = execute_professional_run(DB, request, progress=progress,
                cancel_check=cancelled, trial_checkpoint=checkpoint)
            if build_version() != manifest["code_version"] or result["snapshot"] != arm["snapshot"]:
                raise ValueError("Code or snapshot changed during execution")
            if trial_count != arm["combinations"]:
                raise ValueError("Incomplete trial audit")
            write_once(evidence_dir / "result.json", result)
            store.update(run_id, status="done", phase="DONE", progress=100, result=result,
                verdict=result["verdict"], message=result["verdict_label"], candidate_eligible=False,
                can_claim_edge=False, report_markdown=result["report_markdown"])
            selected = result.get("selected") or {}
            summary = {"run_id": run_id, "status": "done", "verdict": result["verdict"],
                "selected": selected, "cost_stress": result.get("cost_stress"),
                "baselines": result.get("baselines"), "wf": result.get("wf"),
                "path_analysis": result.get("path_analysis"), "market_comparison": result.get("market_comparison")}
        except Exception as exc:  # noqa: BLE001 -- persist failed research before continuing the frozen batch
            stop_batch = isinstance(exc, ResearchCancelled) or cancelled()
            status = "cancelled" if stop_batch else "error"
            message = sanitize_error(exc)[:500]
            store.update(run_id, status=status, phase="STOPPED", message=message,
                         candidate_eligible=False, can_claim_edge=False)
            summary = {"run_id": run_id, "status": status, "error": message}
        summary.update(seconds=round(time.monotonic() - started, 1), completed_combinations=trial_count)
        write_once(evidence_dir / "summary.json", summary)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if stop_batch:
            break
    ledger_unchanged = ledger_fingerprint(DB) == manifest["ledger_before"]
    result = {"protocol": PROTOCOL, "finished_at": now(), "stopped": stop_batch,
        "planned_arms": len(manifest["arms"]), "arms": summaries, "ledger_unchanged": ledger_unchanged,
        "candidate_eligible": False, "can_claim_edge": False}
    write_once(OUTPUT / "summary.json", result)
    return 0 if not stop_batch and ledger_unchanged and all(row["status"] == "done" for row in summaries) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if ROOT.name != "accumulation_breakout" or not DB.is_file():
        raise ValueError("This runner requires the existing AB repository and database")
    if args.prepare:
        prepare()
        return 0
    return execute()


if __name__ == "__main__":
    raise SystemExit(main())
