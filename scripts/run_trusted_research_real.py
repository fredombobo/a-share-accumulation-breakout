"""真实可信研究运行入口（阶段 2）：full 窗网格 + 正式统计块 + 双基线。

用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\run_trusted_research_real.py
    [--max-codes 400] [--step 10] [--strategy A] [--out runtime/v2/research]

产出：runtime/v2/research/trusted_report_<run_id>.json（含 v2_statistics 正式统计块）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.research.promotion_v2 import ROBUST_PROFILE
from ab_screener.research.regime_filter import (
    ATTACK_ONLY_REGIME_ENTRY_POLICY,
    PRODUCTION_REGIME_ENTRY_POLICY,
)
from ab_screener.research.registry import (
    register_experiment,
    register_trial,
    transition_experiment_status,
)
from ab_screener.research.store import ResearchRunStore
from ab_screener.research.trusted_run import (
    COST_VERSION,
    execute_trusted_research,
    input_fingerprint,
    prepare_trusted_pit_snapshot,
    prepare_trusted_regime_filter,
    trusted_portfolio_identity,
)
from research_windows import recommend_research_plan

_TZ = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser(description="真实可信研究（full 窗 + 正式统计）")
    parser.add_argument("--max-codes", type=int, default=600)
    parser.add_argument("--step", type=int, default=5)
    parser.add_argument("--strategy", default="A")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--out", default="runtime/v2/research")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--regime-entry-policy",
        choices=(PRODUCTION_REGIME_ENTRY_POLICY, ATTACK_ONLY_REGIME_ENTRY_POLICY),
        default=PRODUCTION_REGIME_ENTRY_POLICY,
        help="生产一致门禁或预登记的仅进攻市况开仓实验",
    )
    args = parser.parse_args()

    from build_version import build_version

    db_path = Path(args.db).resolve()
    os.environ["AB_DB_PATH"] = str(db_path)
    plan = recommend_research_plan()
    plan_d = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    windows = {
        "is_start": plan_d["is_start"],
        "is_end": plan_d["is_end"],
        "oos_start": plan_d["oos_start"],
        "oos_end": plan_d["oos_end"],
        "mode": plan_d["mode"],
        "automatic_window": True,  # recommend_research_plan 即自动窗
        "wf_windows": plan_d.get("wf_windows") or [],
    }
    if windows["mode"] != "full" or not plan_d.get("data_ready_for_edge_validation"):
        print(
            f"[research] refused: mode={windows['mode']} n_dates={plan_d.get('n_dates')} "
            "(authoritative run requires full data)",
            file=sys.stderr,
        )
        return 2
    print(
        f"[research] mode={windows['mode']} IS={windows['is_start']}~{windows['is_end']}"
        f" OOS={windows['oos_start']}~{windows['oos_end']}"
    )

    run_id = args.run_id or uuid.uuid4().hex[:12]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    max_codes = max(20, min(args.max_codes, 4500))
    pit_snapshot = prepare_trusted_pit_snapshot(
        db_path,
        windows=windows,
        max_codes=max_codes,
    )
    regime_filter = prepare_trusted_regime_filter(
        pit_snapshot,
        windows=windows,
        entry_policy=args.regime_entry_policy,
    )
    request = {
        "strategy": args.strategy,
        "mode": "grid",
        "max_codes": max_codes,
        "step": args.step,
        "preregistered": True,
        "promotion_profile": ROBUST_PROFILE,
        "primary_baseline": "ma20_60",
        "portfolio_model": trusted_portfolio_identity(),
        "pit_snapshot": pit_snapshot.identity(),
        "market_regime_filter": regime_filter.identity(),
    }
    dataset_version = pit_snapshot.dataset_fingerprint
    code_version = build_version()
    input_hash = input_fingerprint(
        request,
        windows,
        dataset_version=dataset_version,
        code_version=code_version,
        cost_version=COST_VERSION,
    )
    registration = {
        "request": request,
        "windows": windows,
        "dataset_version": dataset_version,
        "code_version": code_version,
        "cost_version": COST_VERSION,
        "portfolio_model": request["portfolio_model"],
        "market_regime_filter": request["market_regime_filter"],
        "entry_policy": "next_tradable_open",
        "market_regime_entry_policy": args.regime_entry_policy,
        "selection_rule": "freeze_is_winner_before_oos",
    }
    store = ResearchRunStore(db_path)
    with sqlite3.connect(db_path) as conn:
        experiment_id = register_experiment(
            conn,
            strategy=args.strategy,
            params=registration,
            config_hash=input_hash,
        )
    run_created = False
    try:
        store.create_run(
            run_id,
            strategy=args.strategy,
            research_mode=str(windows["mode"]),
            request={
                **request,
                "_windows": windows,
                "preregistered": True,
                "experiment_id": experiment_id,
            },
            input_hash=input_hash,
            dataset_version=dataset_version,
            code_version=code_version,
            cost_version=COST_VERSION,
            config_hash=input_hash,
        )
        run_created = True
        with sqlite3.connect(db_path) as conn:
            transition_experiment_status(conn, experiment_id, "RUNNING")
    except Exception:
        # A run that never acquired the single-worker slot must not advertise RUNNING.
        # The immutable registration remains available for diagnosis/retry.
        with sqlite3.connect(db_path) as conn:
            transition_experiment_status(conn, experiment_id, "REJECTED")
        raise
    print(f"[research] run_id={run_id} code={code_version} dataset={dataset_version}")

    last_progress = -1

    def on_phase(phase: str, pct: int, message: str, state: dict) -> None:
        nonlocal last_progress
        progress = max(0, min(int(pct), 99))
        if progress != last_progress:
            store.update(
                run_id,
                status="running",
                phase=phase,
                progress=progress,
                message=str(message)[:500],
                checkpoint=state,
                heartbeat_at=datetime.now(_TZ).isoformat(timespec="seconds"),
            )
            last_progress = progress
        if progress % 10 == 0 or phase == "REPORT":
            print(f"[{phase} {progress:>3}%] {message}")

    try:
        result = execute_trusted_research(
            research_run_id=run_id,
            request=request,
            windows=windows,
            db_path=db_path,
            code_version=code_version,
            dataset_version=dataset_version,
            phase_cb=on_phase,
            cancel_check=lambda: store.is_cancel_requested(run_id),
        )
    except Exception as exc:
        if run_created:
            store.update(
                run_id,
                status="error",
                phase="ERROR",
                message=f"{type(exc).__name__}: {str(exc)[:400]}",
            )
        with sqlite3.connect(db_path) as conn:
            register_trial(
                conn,
                experiment_id=experiment_id,
                params=request,
                status="FAILED",
                outcome={"error_type": type(exc).__name__, "message": str(exc)[:400]},
            )
            transition_experiment_status(conn, experiment_id, "REJECTED")
        raise
    report = result.get("report") or {}
    report["experiment_id"] = experiment_id
    path = out_dir / f"trusted_report_{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[research] report saved: {path}")
    gate = report
    markdown = str(report.get("markdown") or "")
    report_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    oos_by_param = {
        str(row.get("param_id")): row for row in (result.get("oos") or []) if row.get("param_id") is not None
    }
    with sqlite3.connect(db_path) as conn:
        trial_rows = result.get("is_all") or []
        if trial_rows:
            for row in trial_rows:
                params = {
                    key: row.get(key)
                    for key in (
                        "strategy",
                        "vol_ratio_min",
                        "strong_reset",
                        "exit_window",
                        "stop_pct",
                    )
                }
                register_trial(
                    conn,
                    experiment_id=experiment_id,
                    params=params,
                    status="COMPLETED",
                    outcome={
                        "is": row,
                        "oos": oos_by_param.get(str(row.get("param_id"))),
                    },
                )
        else:
            register_trial(
                conn,
                experiment_id=experiment_id,
                params=request,
                status="REJECTED",
                outcome={"reason": "研究未产生可评估参数组合"},
            )
        transition_experiment_status(
            conn,
            experiment_id,
            "COMPLETED" if gate.get("verdict") == "PASS" else "REJECTED",
        )
    store.update(
        run_id,
        status="done",
        phase="DONE",
        progress=100,
        message="可信研究完成",
        checkpoint=result.get("checkpoint") or {},
        result=result,
        is_rows=result.get("is_all") or [],
        oos_rows=result.get("oos") or [],
        baselines=result.get("baselines") or {},
        promotion=result.get("promotion_checks") or {},
        verdict=str(gate.get("verdict") or "INSUFFICIENT_EVIDENCE"),
        candidate_eligible=bool(gate.get("candidate_eligible")),
        can_claim_edge=bool(gate.get("candidate_eligible")),
        report_markdown=markdown,
        report_sha256=report_sha256,
    )
    print(f"[research] gate: {gate.get('verdict')} reasons={gate.get('reasons')}")
    stats = report.get("v2_statistics") or {}
    print(
        f"[research] v2_statistics: status={stats.get('status')}"
        + (
            f" dsr={stats.get('dsr')} mintrl={stats.get('min_track_record_length')}"
            if stats.get("status") == "OK"
            else f" reason={stats.get('reason')}"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
