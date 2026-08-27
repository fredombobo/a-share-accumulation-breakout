"""Collect server-side evidence for the seven institutional readiness gates.

The public endpoint never accepts gate booleans from a browser.  D, R and O are
derived from their authoritative local stores; S, P, L and G consume signed
manager evidence artifacts bound to the current code/config/database identity.
Missing evidence is INSUFFICIENT and therefore fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.application.platform_config import resolved_hash
from ab_screener.application.release_evidence import (
    current_release_identity,
    evaluate_release_readiness,
    load_latest_gate_report,
)
from ab_screener.domain.readiness import GATES, ReadinessInput, evaluate_readiness
from ab_screener.operations.backup import backup_ok

_TZ = ZoneInfo("Asia/Shanghai")
_ARTIFACT_GATES = ("S", "P", "L", "G")
_VALID_GATE_STATUS = {"PASS", "FAIL", "INSUFFICIENT"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve_under_root(root: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _insufficient(gate: str, source: str, reason: str) -> dict[str, Any]:
    return {
        "gate": gate,
        "status": "INSUFFICIENT",
        "passed": False,
        "source": source,
        "reason": reason,
        "identity_matches": True,
    }


def _data_gate(identity: dict[str, Any], db_path: Path) -> dict[str, Any]:
    report = load_latest_gate_report(db_path)
    release = evaluate_release_readiness(identity, report)
    blocker_codes = [str(item.get("code")) for item in release["blockers"]]
    missing_or_stale = {
        "REAL_DATA_GATE_NOT_RUN",
        "GATE_TIME_INVALID",
        "GATE_EXPIRED",
        "GATE_REPORT_UNSIGNED",
    }
    mismatch = any(code.endswith("_MISMATCH") for code in blocker_codes)
    if release["ready"]:
        status = "PASS"
    elif blocker_codes and set(blocker_codes).issubset(missing_or_stale | {"WORKTREE_DIRTY"}):
        status = "INSUFFICIENT"
    else:
        status = "FAIL"
    return {
        "gate": "D",
        "status": status,
        "passed": status == "PASS",
        "source": "pt_gate_report",
        "reason": "真实数据门禁与当前发布身份一致" if status == "PASS" else "；".join(
            str(item.get("message")) for item in release["blockers"]
        ),
        "blockers": release["blockers"],
        "report_sha256": release.get("gate_report_sha256"),
        "generated_at": release.get("gate_generated_at"),
        "identity_matches": not mismatch,
    }


def _research_gate(
    db_path: Path,
    research_run_id: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    if not research_run_id:
        return _insufficient("R", "research_runs", "未配置权威研究任务 ID")
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
            row = conn.execute(
                "SELECT research_run_id,status,verdict,candidate_eligible,report_sha256,"
                "code_version,config_hash,dataset_version,cost_version,finished_at "
                "FROM research_runs WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        return _insufficient("R", "research_runs", f"研究证据不可读: {str(exc)[:120]}")
    if row is None:
        return _insufficient("R", "research_runs", f"权威研究任务不存在: {research_run_id}")

    run_status, verdict = str(row[1] or ""), str(row[2] or "")
    code_version = str(row[5] or "")
    identity_matches = bool(code_version) and code_version == identity.get("code_version")
    complete = (
        run_status == "done"
        and bool(row[4])
        and bool(code_version)
        and bool(row[6])
        and bool(row[7])
        and bool(row[8])
    )
    passed = complete and verdict == "PASS" and bool(row[3]) and identity_matches
    if not complete:
        status, reason = "INSUFFICIENT", "权威研究任务尚无完整、带哈希的完成报告"
    elif verdict == "PASS" and not identity_matches:
        status, reason = "FAIL", "权威研究报告不属于当前代码版本"
    elif passed:
        status, reason = "PASS", "权威研究报告通过且允许进入候选隔离区"
    else:
        status, reason = "FAIL", f"权威研究结论为 {verdict or 'UNKNOWN'}，不得晋级"
    return {
        "gate": "R",
        "status": status,
        "passed": passed,
        "source": "research_runs",
        "reason": reason,
        "identity_matches": identity_matches,
        "research_run_id": row[0],
        "run_status": run_status,
        "verdict": verdict or None,
        "candidate_eligible": bool(row[3]),
        "report_sha256": row[4],
        "code_version": row[5],
        "research_config_hash": row[6],
        "dataset_version": row[7],
        "cost_version": row[8],
        "finished_at": row[9],
    }


def _latest_artifact(evidence_dir: Path, gate: str) -> Path | None:
    if not evidence_dir.is_dir():
        return None
    candidates = [
        path for path in evidence_dir.glob(f"{gate}*.json") if path.is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _artifact_gate(
    gate: str,
    evidence_dir: Path,
    identity: dict[str, Any],
) -> dict[str, Any]:
    path = _latest_artifact(evidence_dir, gate)
    if path is None:
        return _insufficient(gate, "gate_artifact", f"缺少 {gate} 闸门证据文件")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            **_insufficient(gate, "gate_artifact", f"证据文件不可读: {str(exc)[:100]}"),
            "status": "FAIL",
            "path": str(path),
        }
    if not isinstance(report, dict) or str(report.get("gate")) != gate:
        return {
            **_insufficient(gate, "gate_artifact", "证据 gate 字段不匹配"),
            "status": "FAIL",
            "path": str(path),
        }
    signature = str(report.get("evidence_sha256") or "")
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    expected_signature = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    if not signature or signature != expected_signature:
        return {
            **_insufficient(gate, "gate_artifact", "证据 SHA-256 缺失或校验失败"),
            "status": "FAIL",
            "path": str(path),
        }

    raw_identity = report.get("identity")
    report_identity: dict[str, Any] = raw_identity if isinstance(raw_identity, dict) else {}
    expected_identity = {
        "git_sha": identity.get("git_sha"),
        "code_version": identity.get("code_version"),
        "platform_config_hash": identity.get("platform_config_hash"),
        "db_fingerprint": identity.get("db_fingerprint"),
    }
    mismatches = [
        key for key, expected in expected_identity.items()
        if not report_identity.get(key) or report_identity.get(key) != expected
    ]
    raw_status = str(report.get("status") or "INSUFFICIENT").upper()
    status = raw_status if raw_status in _VALID_GATE_STATUS else "FAIL"
    if mismatches:
        status = "FAIL"
    return {
        "gate": gate,
        "status": status,
        "passed": status == "PASS" and not mismatches,
        "source": "gate_artifact",
        "reason": (
            f"证据身份不匹配: {', '.join(mismatches)}"
            if mismatches else str(report.get("summary") or f"{gate} 闸门 {status}")
        ),
        "identity_matches": not mismatches,
        "identity_mismatches": mismatches,
        "path": str(path),
        "generated_at": report.get("generated_at"),
        "evidence_sha256": signature,
    }


def _operations_gate(
    root: Path,
    soak_dir: Path,
    gate_dir: Path,
    identity: dict[str, Any],
) -> dict[str, Any]:
    from scripts.soak_monitor_v2 import _day_counts, _read_evidence

    matching_days: list[str] = []
    mismatched_files: list[str] = []
    invalid_files: list[str] = []
    if soak_dir.is_dir():
        for path in sorted(soak_dir.glob("*.json")):
            evidence = _read_evidence(path)
            if evidence is None:
                invalid_files.append(path.name)
                continue
            evidence_identity = evidence.get("identity") or {}
            if (
                evidence_identity.get("code_version") != identity.get("git_sha")
                or evidence_identity.get("config_hash") != identity.get("platform_config_hash")
            ):
                mismatched_files.append(path.name)
                continue
            if _day_counts(evidence):
                matching_days.append(str(evidence["trade_date"]))
    matching_days = sorted(set(matching_days))

    backup_root = os.environ.get("AB_BACKUP_ROOT")
    if backup_root:
        backup = backup_ok(backup_root)
    else:
        backup = {"ok": False, "reason": "AB_BACKUP_ROOT 未配置"}
    operations_artifact = _artifact_gate("O", gate_dir, identity)
    soak_pass = len(matching_days) >= 5
    if not backup.get("ok"):
        status = "FAIL"
        reason = f"备份门禁未通过: {backup.get('reason') or backup}"
    elif not soak_pass:
        status = "INSUFFICIENT"
        reason = f"当前身份仅有 {len(matching_days)}/5 个真实完成交易日"
    elif not operations_artifact["passed"]:
        status = str(operations_artifact["status"])
        reason = "缺少当前身份的严格恢复演练与运维验收证据"
    else:
        status = "PASS"
        reason = "备份与五个真实完成交易日日终观察均通过"
    return {
        "gate": "O",
        "status": status,
        "passed": status == "PASS",
        "source": "backup_and_soak",
        "reason": reason,
        "identity_matches": bool(operations_artifact.get("identity_matches", True)),
        "backup": backup,
        "operations_artifact": operations_artifact,
        "soak": {
            "count": len(matching_days),
            "required": 5,
            "completed_trade_days": matching_days,
            "mismatched_files": mismatched_files,
            "invalid_files": invalid_files,
            "directory": str(soak_dir),
        },
        "project_root": str(root),
    }


def build_readiness_snapshot(
    project_root: str | Path,
    db_path: str | Path,
    resolved_config: dict[str, Any],
) -> dict[str, Any]:
    """Build the fail-closed readiness payload from server-side evidence."""
    root = Path(project_root).resolve()
    db = Path(db_path).resolve()
    identity = current_release_identity(root, db)
    identity["platform_config_hash"] = resolved_config.get("resolved_hash") or resolved_hash(
        resolved_config
    )
    evidence_config = resolved_config.get("evidence") or {}
    gate_dir = _resolve_under_root(
        root, str(evidence_config.get("gate_evidence_dir") or "runtime/v2/gates")
    )
    soak_dir = _resolve_under_root(
        root, str(evidence_config.get("soak_evidence_dir") or "runtime/v2/soak")
    )
    research_run_id = str(evidence_config.get("authoritative_research_run_id") or "")

    gates: dict[str, dict[str, Any]] = {
        "D": _data_gate(identity, db),
        "R": _research_gate(db, research_run_id, identity),
        "O": _operations_gate(root, soak_dir, gate_dir, identity),
    }
    for gate in _ARTIFACT_GATES:
        gates[gate] = _artifact_gate(gate, gate_dir, identity)

    identity_matches = all(bool(gates[gate].get("identity_matches", True)) for gate in GATES)
    verdict = evaluate_readiness(
        ReadinessInput(
            gate_results={gate: bool(gates[gate]["passed"]) for gate in GATES},
            worktree_clean=bool(identity.get("worktree_clean")),
            identity_matches=identity_matches,
        )
    )
    return {
        **verdict,
        "gates": {gate: gates[gate] for gate in GATES},
        "identity": identity,
        "authoritative_research_run_id": research_run_id or None,
        "evidence_directory": str(gate_dir),
        "evaluated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "live_trading_enabled": False,
    }
