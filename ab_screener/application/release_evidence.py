"""Release-candidate evidence for the local personal workflow.

Runtime freshness and a historical real-data PASS are deliberately insufficient.
A release is ready only when the latest gate is recent and was generated from the
exact current code, configuration and database identity while the Git worktree is
clean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
_MAX_GATE_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(minutes=5)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def current_release_identity(project_root: str | Path, db_path: str | Path) -> dict[str, Any]:
    """Return the exact local identity to which a real-data gate must belong."""
    root = Path(project_root).resolve()
    from build_version import build_version
    from paper_trading.real_data_gate import _config_hash, _db_fingerprint

    try:
        git_sha = _run_git(root, "rev-parse", "HEAD")
        status = _run_git(
            root,
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    except (OSError, subprocess.CalledProcessError):
        git_sha = "unknown"
        status = "GIT_IDENTITY_UNAVAILABLE"
    clean = not status
    worktree_fingerprint = _sha256_text(
        _canonical_json({"git_sha": git_sha, "status": status or "CLEAN"})
    )
    return {
        "git_sha": git_sha,
        "worktree_clean": clean,
        "worktree_fingerprint": worktree_fingerprint,
        "code_version": build_version(),
        "config_hash": _config_hash(),
        "db_fingerprint": _db_fingerprint(Path(db_path)),
    }


def load_latest_gate_report(db_path: str | Path) -> dict[str, Any] | None:
    """Load the newest immutable real-data gate payload from SQLite."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT passed, data_version, issues_json, report_json, code_version,"
                " config_hash, report_sha256, generated_at FROM pt_gate_report "
                "ORDER BY report_id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    if row[3]:
        try:
            report = json.loads(row[3])
            if isinstance(report, dict):
                return report
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "passed": bool(row[0]),
        "status": "PASS" if row[0] else "FAIL",
        "local_latest_trade_date": row[1],
        "issues": json.loads(row[2] or "[]"),
        "code_version": row[4],
        "config_hash": row[5],
        "report_sha256": row[6],
        "generated_at": row[7],
    }


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TZ)
    return parsed.astimezone(_TZ)


def evaluate_release_readiness(
    identity: dict[str, Any],
    gate_report: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join local identity and gate evidence into one fail-closed verdict."""
    current_time = (now or datetime.now(_TZ)).astimezone(_TZ)
    blockers: list[dict[str, str]] = []

    def block(code: str, message: str) -> None:
        blockers.append({"code": code, "message": message})

    if not identity.get("worktree_clean"):
        block("WORKTREE_DIRTY", "工作区存在未提交变更")
    if gate_report is None:
        block("REAL_DATA_GATE_NOT_RUN", "尚无真实数据门禁报告")
    else:
        if not gate_report.get("passed"):
            block("REAL_DATA_GATE_FAILED", "最近一次真实数据门禁未通过")
        generated_at = _parse_time(gate_report.get("generated_at"))
        if generated_at is None:
            block("GATE_TIME_INVALID", "真实数据门禁缺少有效生成时间")
        elif generated_at - current_time > _MAX_FUTURE_SKEW:
            block("GATE_TIME_INVALID", "真实数据门禁生成时间晚于当前系统时间")
        elif current_time - generated_at > _MAX_GATE_AGE:
            block("GATE_EXPIRED", "真实数据门禁报告已超过 24 小时")
        comparisons = (
            ("code_version", "CODE_VERSION_MISMATCH", "门禁报告不属于当前构建版本"),
            ("config_hash", "CONFIG_HASH_MISMATCH", "门禁报告不属于当前配置"),
            (
                "db_fingerprint",
                "DATABASE_FINGERPRINT_MISMATCH",
                "门禁报告不属于当前行情数据库",
            ),
        )
        for field, code, message in comparisons:
            if gate_report.get(field) != identity.get(field):
                block(code, message)
        if not gate_report.get("report_sha256"):
            block("GATE_REPORT_UNSIGNED", "真实数据门禁报告缺少 SHA-256")

    ready = not blockers
    return {
        "status": "PASS" if ready else "FAIL",
        "ready": ready,
        "blockers": blockers,
        "identity": identity,
        "gate_report_sha256": gate_report.get("report_sha256") if gate_report else None,
        "gate_generated_at": gate_report.get("generated_at") if gate_report else None,
        "checked_at": current_time.isoformat(timespec="seconds"),
    }


def build_release_evidence(project_root: str | Path, db_path: str | Path) -> dict[str, Any]:
    identity = current_release_identity(project_root, db_path)
    gate_report = load_latest_gate_report(db_path)
    return evaluate_release_readiness(identity, gate_report)


def write_release_evidence(evidence: dict[str, Any], output_dir: str | Path) -> Path:
    """Write a signed, immutable-by-name evidence artifact."""
    payload = dict(evidence)
    payload["evidence_sha256"] = _sha256_text(_canonical_json(payload))
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = str(payload.get("checked_at") or datetime.now(_TZ).isoformat()).replace(":", "")
    stamp = stamp.replace("-", "")[:15]
    path = destination / f"release_evidence_{stamp}_{payload['evidence_sha256'][:12]}.json"
    if not path.exists():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成当前版本的发布就绪证据")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--output", default="runtime/releases")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = root / db_path
    evidence = build_release_evidence(root, db_path)
    path = write_release_evidence(evidence, root / args.output)
    print(json.dumps({**evidence, "evidence_path": str(path)}, ensure_ascii=False, indent=2))
    return 0 if evidence["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
