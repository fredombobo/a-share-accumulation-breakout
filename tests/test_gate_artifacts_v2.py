from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import scripts.build_gate_artifacts_v2 as gates


def _signed_transport(path: Path) -> None:
    payload = {
        "schema": "vendor-transport-evidence-v2",
        "status": "PASS",
        "endpoint": {
            "scheme": "https",
            "certificate_verified": True,
            "hostname_verified": True,
        },
    }
    payload["evidence_sha256"] = hashlib.sha256(
        gates._canonical(payload).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_gate_builder_binds_every_artifact_to_current_identity(tmp_path, monkeypatch):
    db = tmp_path / "production.db"
    sqlite3.connect(db).close()
    transport = tmp_path / "transport.json"
    _signed_transport(transport)
    restore = tmp_path / "restore.json"
    restore.write_text(
        json.dumps(
            {
                "status": "PASS",
                "integrity": "ok",
                "table_hashes_match": True,
                "rto_pass": True,
            }
        ),
        encoding="utf-8",
    )
    anchors = tmp_path / "anchors"
    anchors.mkdir()
    (anchors / "audit-anchor-current.sig").write_text("anchor", encoding="utf-8")
    key = tmp_path / "audit.key"
    key.write_bytes(b"x" * 32)
    output = tmp_path / "gates"

    identity = {
        "git_sha": "git-current",
        "code_version": "build-current",
        "db_fingerprint": "db-current",
        "worktree_clean": True,
    }
    monkeypatch.setattr(gates, "current_release_identity", lambda *_: dict(identity))
    monkeypatch.setattr(
        gates,
        "load_resolved_config",
        lambda: {
            "resolved_hash": "platform-current",
            "flags": {"V2_STRATEGY_REGISTRY_ENABLED": True},
        },
    )
    monkeypatch.setattr(gates, "selection_plugin_ids", lambda: [f"s{i}" for i in range(6)])
    monkeypatch.setattr(gates, "backup_ok", lambda *_: {"ok": True, "count": 7})
    monkeypatch.setattr(gates, "verify_chain_head", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        gates,
        "_db_snapshot",
        lambda *_: {
            "latest_market": "20260827",
            "counts": {
                "signal_observations": 6,
                "signal_outcomes": 0,
                "risk_snapshots": 1,
                "audit_events": 3,
            },
            "outcome_status": {},
            "risk": {
                "trade_date": "20260827",
                "market_version": "daily:20260827",
                "rule_version": "risk-v2",
                "config_version": "robust_personal_v2",
                "status": "INSUFFICIENT",
            },
            "manifest": ["m", "20260827", "COMPLETE"],
            "cycle": ["20260827", "DONE"],
            "reconciliation": ["20260827", "OK"],
            "dag": ["d", "20260827", "COMPLETED"],
            "audit": {"valid": True, "events": 3},
        },
    )

    paths = gates.build_artifacts(
        db_path=db,
        output_dir=output,
        transport_report=transport,
        backup_root=tmp_path,
        restore_report=restore,
        anchor_dir=anchors,
        signing_key_file=key,
    )

    assert set(paths) == {"S", "P", "L", "O", "G"}
    statuses = {}
    for gate, path in paths.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        signature = payload.pop("evidence_sha256")
        assert signature == hashlib.sha256(
            gates._canonical(payload).encode("utf-8")
        ).hexdigest()
        assert payload["identity"] == {
            "git_sha": "git-current",
            "code_version": "build-current",
            "platform_config_hash": "platform-current",
            "db_fingerprint": "db-current",
        }
        statuses[gate] = payload["status"]
    assert statuses == {
        "S": "INSUFFICIENT",
        "P": "PASS",
        "L": "PASS",
        "O": "PASS",
        "G": "PASS",
    }
