"""Execute exactly the two preregistered arms; a failed research result is retained."""
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

from ab_screener.research.intermediate_momentum import MECHANISM_ID
from ab_screener.research.professional_grid import request_hash
from ab_screener.research.professional_runner import (
    execute_professional_run,
    prepare_professional_request,
)
from ab_screener.research.resilient_absorption import (
    BASE_ENTRY_MECHANISM_ID,
    entry_mechanism_identity,
)
from ab_screener.research.store import ResearchRunStore
from ab_screener.research.trusted_run import COST_VERSION
from build_version import build_version

PREREG = ROOT / "docs/MARKET-EXCESS-MOMENTUM-PREREGISTRATION-2026-09-05.md"
SOURCE = "probt-49462a73e38d"


def evaluate_pair(control: dict[str, Any], factor: dict[str, Any]) -> dict[str, Any]:
    """Apply the predeclared target, not a post-hoc choice of profitable windows."""
    same = control.get("snapshot") == factor.get("snapshot") and all(
        control["request"][key] == factor["request"][key]
        for key in ("windows", "universe", "parameters", "knowledge_cutoff", "code_version", "sample_step"))
    cm = control.get("market_comparison") or {}
    fm = factor.get("market_comparison") or {}
    complete = cm.get("status") == fm.get("status") == "COMPLETE"
    checks: dict[str, bool] = {"same_protocol_and_snapshot": same, "aligned_market_evidence": complete}
    improvement = None
    if complete:
        oos = fm["oos"]
        improvement = oos["strategy_return"] - cm["oos"]["strategy_return"]
        checks.update(
            at_least_200_sessions=oos["sessions"] >= 200,
            excess_at_least_10pp=oos["excess_return"] >= 0.10,
            factor_improvement_at_least_3pp=improvement >= 0.03,
            double_cost_positive=(factor["cost_stress"]["metrics"]["net_total_return"] > 0),
            double_cost_beats_index=(oos.get("stress_excess_return") is not None and oos["stress_excess_return"] > 0),
            drawdown_at_most_25pct=oos["strategy_max_drawdown"] <= 0.25,
            at_least_30_oos_trades=factor["selected"]["oos"]["net_n_trades"] >= 30,
            wf_sample_complete=bool((factor.get("wf") or {}).get("evidence_complete")),
            wf_beats_index_at_least_two_of_three=(len(fm.get("wf", [])) == 3 and sum(
                row.get("excess_return") is not None and row["excess_return"] > 0
                for row in fm["wf"]) >= 2),
        )
    return {"status": "HISTORICAL_TARGET_MET" if all(checks.values()) else "TARGET_NOT_MET",
            "checks": checks, "factor_improvement": improvement,
            "candidate_eligible": False, "can_claim_edge": False,
            "notice": "历史已观察；即使达到目标也不是未见样本验证，不自动修改每日选股。"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/stock_data.db")
    parser.add_argument("--execute", action="store_true", help="明确执行两个冻结研究任务")
    args = parser.parse_args()
    db = args.db.resolve()
    if ROOT.name != "accumulation_breakout" or db != ROOT / "runtime/stock_data.db":
        raise ValueError("只允许此预登记绑定的 AB/8001 数据库")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        source = json.loads(conn.execute("SELECT result_json FROM research_runs WHERE research_run_id=?", (SOURCE,)).fetchone()[0])
    payload = {"strategy": "A", "sample_step": 1, "max_codes": 100,
               "parameters": source["request"]["parameters"],
               "universe": {"codes": source["request"]["universe"]["codes"]},
               "conditions": [], "windows": {"mode": "auto"}}
    prepared = prepare_professional_request(db, payload)
    if not prepared["data_scope"]["can_run"] or prepared["windows"] != source["request"]["windows"]:
        raise ValueError("数据或窗口与冻结方案不同，停止，不自动换样本")
    prepared["knowledge_cutoff"] = source["snapshot"]["decision_at"]
    prereg_hash = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    prepared["research_protocol"] = {"id": "MOMENTUM-PAIR-20260905-V1", "source": SOURCE,
                                     "preregistration_sha256": prereg_hash, "holdout_already_observed": True}
    version = build_version()
    prepared["code_version"] = version
    prepared["input_hash"] = request_hash(prepared)
    print(json.dumps({"code_version": version, "preregistration_sha256": prereg_hash,
                      "stocks": prepared["universe"]["count"], "sample_step": 1,
                      "windows": prepared["windows"], "execute": args.execute}, ensure_ascii=False), flush=True)
    if not args.execute:
        return 0
    output = ROOT / "runtime/research/momentum-pair-20260905-v1"
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"prepared": prepared, "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "preregistration_sha256": prereg_hash, "code_version": version}
    manifest_path = output / "registration.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if prior["prepared"] != prepared:
            raise ValueError("本轮已登记其它身份，禁止覆盖登记；先审查失败原因")
    else:
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    store = ResearchRunStore(db)
    results = []
    for label, mechanism in [("control", BASE_ENTRY_MECHANISM_ID), ("momentum", MECHANISM_ID)]:
        request = deepcopy(prepared)
        request["entry_mechanism"] = entry_mechanism_identity(mechanism)
        request["research_boundary"] = {"mode": "PREREGISTERED_HISTORICAL_DIAGNOSTIC", "candidate_eligible": False,
                                        "note": "同条件配对诊断，已观察历史不能冒充未见样本。"}
        request["input_hash"] = request_hash(request)
        run_id = f"probt-mom6m-v1-{label}"
        existing = store.get(run_id)
        if existing:
            if existing["input_hash"] != request["input_hash"] or existing["status"] != "done":
                raise ValueError(f"{run_id} 尚未完成或身份不同，不自动重试/覆盖")
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
            if build_version() != version:
                raise ValueError("运行期间代码身份变化，结果不能作为同身份配对验收")
            store.update(run_id, status="done", phase="DONE", progress=100, result=result,
                         verdict=result["verdict"], message=result["verdict_label"],
                         candidate_eligible=False, can_claim_edge=False, report_markdown=result["report_markdown"])
        except Exception as exc:
            from optimizer import ResearchCancelled
            from tushare_init import sanitize_error

            store.update(run_id, status="cancelled" if isinstance(exc, ResearchCancelled) else "error",
                         phase="STOPPED", message=sanitize_error(exc)[:500], candidate_eligible=False, can_claim_edge=False)
            raise
        results.append(result)
        with (output / f"{label}.json").open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    summary = evaluate_pair(*results)
    summary["run_ids"] = ["probt-mom6m-v1-control", "probt-mom6m-v1-momentum"]
    path = output / "comparison.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
