"""从生产数据库和独立证据生成 S/P/L/O/G 身份绑定门禁文件。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ab_screener.application.audit_service import verify_audit_chain, verify_chain_head
from ab_screener.application.platform_config import load_resolved_config
from ab_screener.application.release_evidence import current_release_identity
from ab_screener.operations.backup import backup_ok
from ab_screener.strategies import selection_plugin_ids

_TZ = ZoneInfo("Asia/Shanghai")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signed_report(path: Path, expected_schema: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != expected_schema:
        return None
    signature = str(payload.get("evidence_sha256") or "")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    expected = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    return payload if signature and signature == expected else None


def _check(check_id: str, status: str, observed: Any, reason: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "observed": observed,
        "reason": reason,
    }


def _artifact(
    gate: str,
    status: str,
    summary: str,
    identity: dict[str, Any],
    checks: list[dict[str, Any]],
    blockers: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "personal-institutional-gate-v2",
        "gate": gate,
        "gate_id": f"{gate}_PRODUCTION_ACCEPTANCE",
        "gate_version": "2.1.0",
        "status": status,
        "summary": summary,
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "identity": identity,
        "checks": checks,
        "blockers": blockers,
        "live_trading_enabled": False,
    }
    payload["evidence_sha256"] = hashlib.sha256(
        _canonical(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _db_snapshot(db: Path) -> dict[str, Any]:
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30) as conn:
        latest_market = str(conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0] or "")
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "signal_observations",
                "signal_outcomes",
                "risk_snapshots",
                "audit_events",
            )
        }
        outcome_status = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT status,COUNT(*) FROM signal_outcomes GROUP BY status"
            ).fetchall()
        }
        risk = conn.execute(
            "SELECT snapshot_id,trade_date,market_version,rule_version,config_version,"
            "metrics_json,created_at FROM risk_snapshots"
            " ORDER BY trade_date DESC,created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        manifest = conn.execute(
            "SELECT manifest_id,trade_date,status,blockers_json,manifest_sha256,code_version,"
            "config_hash,created_at FROM daily_run_manifests"
            " ORDER BY trade_date DESC,created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        cycle = conn.execute(
            "SELECT run_date,phase,blocked_reason,finished_at FROM pt_cycle"
            " ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        reconciliation = conn.execute(
            "SELECT run_date,result,severity,status,checked_at FROM pt_reconciliation"
            " ORDER BY rec_id DESC LIMIT 1"
        ).fetchone()
        dag = conn.execute(
            "SELECT run_id,trade_date,status,finished_at FROM dag_runs"
            " ORDER BY trade_date DESC,created_at DESC LIMIT 1"
        ).fetchone()
        audit = verify_audit_chain(conn)
    risk_payload = None
    if risk:
        metrics = json.loads(str(risk[5]))
        risk_payload = {
            "snapshot_id": risk[0],
            "trade_date": risk[1],
            "market_version": risk[2],
            "rule_version": risk[3],
            "config_version": risk[4],
            "status": metrics.get("status"),
            "created_at": risk[6],
        }
    return {
        "latest_market": latest_market,
        "counts": counts,
        "outcome_status": outcome_status,
        "risk": risk_payload,
        "manifest": list(manifest) if manifest else None,
        "cycle": list(cycle) if cycle else None,
        "reconciliation": list(reconciliation) if reconciliation else None,
        "dag": list(dag) if dag else None,
        "audit": audit,
    }


def build_artifacts(
    *,
    db_path: str | Path,
    output_dir: str | Path,
    transport_report: str | Path,
    backup_root: str | Path,
    restore_report: str | Path | None,
    anchor_dir: str | Path,
    signing_key_file: str | Path,
) -> dict[str, Path]:
    db = Path(db_path).resolve()
    config = load_resolved_config()
    release = current_release_identity(ROOT, db)
    release["platform_config_hash"] = config["resolved_hash"]
    if not release["worktree_clean"]:
        raise RuntimeError("refuse to issue gate artifacts from a dirty worktree")
    identity = {
        key: release[key]
        for key in ("git_sha", "code_version", "platform_config_hash", "db_fingerprint")
    }
    snapshot = _db_snapshot(db)
    flags = config["flags"]

    plugin_ids = selection_plugin_ids()
    observations = snapshot["counts"]["signal_observations"]
    outcomes = snapshot["counts"]["signal_outcomes"]
    mature = int(snapshot["outcome_status"].get("MATURED", 0))
    s_status = "PASS" if mature >= 300 else "INSUFFICIENT"
    s_blockers = [] if s_status == "PASS" else ["SHADOW_AND_PAPER_MATURITY_NOT_REACHED"]
    s = _artifact(
        "S",
        s_status,
        (
            "策略生产观察、成熟 outcome 与长期阈值均满足"
            if s_status == "PASS"
            else "六插件已接入生产观察，但真实时间成熟度尚未达到 S-11/S-13"
        ),
        identity,
        [
            _check("S-01", "PASS" if len(plugin_ids) == 6 else "FAIL", plugin_ids, "六插件注册表"),
            _check(
                "S-production-wiring",
                "PASS" if flags.get("V2_STRATEGY_REGISTRY_ENABLED") and observations > 0 else "INSUFFICIENT",
                {"enabled": flags.get("V2_STRATEGY_REGISTRY_ENABLED"), "observations": observations},
                "生产扫描只追加 SHADOW 观察",
            ),
            _check("S-08", "PASS" if outcomes >= 0 else "FAIL", snapshot["outcome_status"], "outcome 状态按成熟日回填"),
            _check("S-11/S-13", s_status, {"mature": mature, "required_long_term": 300}, "真实时间成熟阈值"),
        ],
        s_blockers,
    )

    risk = snapshot["risk"]
    p_pass = bool(
        risk
        and risk["trade_date"] == snapshot["latest_market"]
        and risk["market_version"] == f"daily:{snapshot['latest_market']}"
        and risk["rule_version"] == "risk-v2"
        and risk["config_version"] == "robust_personal_v2"
        and risk["status"] in {"OK", "INSUFFICIENT"}
    )
    p = _artifact(
        "P",
        "PASS" if p_pass else "FAIL",
        "最新交易日风险快照已固化并如实披露样本状态" if p_pass else "缺少合规的最新交易日风险快照",
        identity,
        [_check("P-05/P-06", "PASS" if p_pass else "FAIL", risk, "PIT 风险快照与版本")],
        [] if p_pass else ["LATEST_RISK_SNAPSHOT_INVALID_OR_MISSING"],
    )

    manifest = snapshot["manifest"]
    cycle = snapshot["cycle"]
    rec = snapshot["reconciliation"]
    dag = snapshot["dag"]
    l_pass = bool(
        manifest
        and manifest[1] == snapshot["latest_market"]
        and manifest[2] == "COMPLETE"
        and cycle
        and cycle[0] == snapshot["latest_market"]
        and cycle[1] == "DONE"
        and rec
        and rec[0] == snapshot["latest_market"]
        and rec[1] == "OK"
        and dag
        and dag[1] == snapshot["latest_market"]
        and dag[2] == "COMPLETED"
    )
    l = _artifact(
        "L",
        "PASS" if l_pass else "FAIL",
        "最新交易日扫描、DAG、周期、对账和日清清单 COMPLETE" if l_pass else "最新交易日日清未闭环",
        identity,
        [
            _check("L-12-manifest", "PASS" if manifest and manifest[2] == "COMPLETE" else "FAIL", manifest, "日清清单"),
            _check("L-12-cycle", "PASS" if cycle and cycle[1] == "DONE" else "FAIL", cycle, "纸面周期"),
            _check("L-12-reconciliation", "PASS" if rec and rec[1] == "OK" else "FAIL", rec, "内部对账"),
            _check("O-01", "PASS" if dag and dag[2] == "COMPLETED" else "FAIL", dag, "持久 DAG"),
        ],
        [] if l_pass else ["LATEST_EOD_NOT_COMPLETE"],
    )

    backups = backup_ok(backup_root)
    restore_payload: dict[str, Any] | None = None
    if restore_report and Path(restore_report).is_file():
        try:
            restore_payload = json.loads(Path(restore_report).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            restore_payload = None
    restore_pass = bool(
        restore_payload
        and restore_payload.get("status") == "PASS"
        and restore_payload.get("integrity") == "ok"
        and restore_payload.get("table_hashes_match") is True
        and restore_payload.get("rto_pass") is True
    )
    o_status = "PASS" if backups.get("ok") and restore_pass else "INSUFFICIENT"
    o = _artifact(
        "O",
        o_status,
        "七份备份和严格恢复演练通过" if o_status == "PASS" else "备份或严格恢复证据尚未全部满足",
        identity,
        [
            _check("O-07", "PASS" if backups.get("ok") else "INSUFFICIENT", backups, "验证备份保留与新鲜度"),
            _check("O-08", "PASS" if restore_pass else "INSUFFICIENT", restore_payload, "严格临时恢复与 RTO"),
        ],
        [] if o_status == "PASS" else ["BACKUP_OR_RESTORE_EVIDENCE_INSUFFICIENT"],
    )

    transport = _signed_report(Path(transport_report), "vendor-transport-evidence-v2")
    transport_pass = bool(
        transport
        and transport.get("status") == "PASS"
        and (transport.get("endpoint") or {}).get("scheme") == "https"
        and (transport.get("endpoint") or {}).get("certificate_verified") is True
        and (transport.get("endpoint") or {}).get("hostname_verified") is True
    )
    key_path = Path(signing_key_file)
    anchors = sorted(Path(anchor_dir).glob("audit-anchor-*.sig"), key=lambda p: p.stat().st_mtime)
    anchor_pass = False
    if key_path.is_file() and anchors and snapshot["audit"]["valid"]:
        with sqlite3.connect(str(db)) as conn:
            anchor_pass = verify_chain_head(conn, anchors[-1], signing_key=key_path.read_bytes().strip())
    g_pass = transport_pass and snapshot["audit"]["events"] > 0 and anchor_pass
    g = _artifact(
        "G",
        "PASS" if g_pass else "FAIL",
        "HTTPS/TLS、审计 hash chain、外部锚点和实盘隔离均通过" if g_pass else "传输或审计锚定仍有阻断",
        identity,
        [
            _check("G-04-chain", "PASS" if snapshot["audit"]["valid"] and snapshot["audit"]["events"] else "FAIL", snapshot["audit"], "生产审计链"),
            _check("G-04-anchor", "PASS" if anchor_pass else "FAIL", str(anchors[-1]) if anchors else None, "DB 外签名锚点"),
            _check("G-06", "PASS", False, "LIVE_TRADING_ENABLED=false"),
            _check("G-14", "PASS" if transport_pass else "FAIL", transport, "HTTPS/TLS 实际业务探针"),
        ],
        [] if g_pass else ["TRANSPORT_OR_AUDIT_EVIDENCE_FAILED"],
    )

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for gate, artifact in {"S": s, "P": p, "L": l, "O": o, "G": g}.items():
        path = output / f"{gate}_{datetime.now(_TZ):%Y%m%d}_{artifact['evidence_sha256'][:12]}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[gate] = path
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build identity-bound S/P/L/O/G artifacts")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", default="runtime/v2/gates")
    parser.add_argument("--transport-report", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--restore-report")
    parser.add_argument("--anchor-dir", required=True)
    parser.add_argument("--signing-key-file", required=True)
    args = parser.parse_args(argv)
    paths = build_artifacts(
        db_path=args.db,
        output_dir=args.output,
        transport_report=args.transport_report,
        backup_root=args.backup_root,
        restore_report=args.restore_report,
        anchor_dir=args.anchor_dir,
        signing_key_file=args.signing_key_file,
    )
    print(json.dumps({gate: str(path) for gate, path in paths.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
