"""P8 soak 监视器 v2（V2R-O2）：只计"真实完成"交易日，O-12 判定。

有效日（同时满足，缺一不计）：
- `dag_runs` 存在 `COMPLETED` 记录（run_id/finished_at 披露）；
- 存在不可变（append-only）`daily_run_manifests` 且 status=COMPLETE（sha256/created_at 披露）；
- 证据文件自校验（file_self_sha256）通过；
- trade_date 为合法 YYYYMMDD（日期伪造不计）。

以下均不计：DAG FAILED、manifest 缺失、manifest PARTIAL、仅 pt_cycle、
仅纸面 cycle、日期伪造、证据文件被篡改。同一天重复收集不增加天数。

每个证据文件披露：交易日、收集时点、代码/配置/数据库身份、DAG run、
manifest hash、对账状态、审计 chain head 与文件自身 SHA-256。

用法：
  .venv312\\Scripts\\python.exe scripts\\soak_monitor_v2.py \\
    --db runtime/stock_data.db --soak-dir runtime/v2/soak \\
    --code-version <git-sha> --config-hash <hash> [--collect 20260818]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
MIN_SOAK_DAYS = 5
_DATE_RE = re.compile(r"^\d{8}$")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _db_identity(db_path: Path) -> dict:
    ident: dict = {"path": str(db_path.resolve())}
    if db_path.is_file():
        ident["size_bytes"] = db_path.stat().st_size
        ident["sha256"] = _sha256_file(db_path)
    else:
        ident["missing"] = True
    return ident


def _valid_trade_date(trade_date: str) -> bool:
    if not _DATE_RE.match(str(trade_date)):
        return False
    try:
        datetime.strptime(str(trade_date), "%Y%m%d")
    except ValueError:
        return False
    return True


def collect_day_evidence(
    db_path: str | Path,
    trade_date: str,
    *,
    code_version: str = "",
    config_hash: str = "",
) -> dict:
    """收集单个交易日的证据摘要（只读，不修改账本）。"""
    if not _valid_trade_date(trade_date):
        raise ValueError(f"trade_date 必须为合法 YYYYMMDD: {trade_date!r}")
    db_path = Path(db_path)
    evidence: dict = {
        "schema": "soak-evidence-v2",
        "trade_date": str(trade_date),
        "collected_at": _now(),
        "identity": {
            "code_version": code_version,
            "config_hash": config_hash,
            "db": _db_identity(db_path),
        },
    }
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "dag_runs" in tables:
            row = conn.execute(
                "SELECT run_id, status, finished_at FROM dag_runs WHERE trade_date=?"
                " ORDER BY created_at DESC LIMIT 1", (trade_date,),
            ).fetchone()
            if row:
                evidence["dag"] = {
                    "run_id": row[0], "status": row[1], "finished_at": row[2],
                }
        if "daily_run_manifests" in tables:
            row = conn.execute(
                "SELECT status, manifest_sha256, created_at, code_version, config_hash"
                " FROM daily_run_manifests WHERE trade_date=?"
                " ORDER BY created_at DESC LIMIT 1", (trade_date,),
            ).fetchone()
            if row:
                manifest = {
                    "status": row[0], "sha256": row[1], "created_at": row[2],
                }
                if row[3]:
                    evidence["identity"]["code_version"] = row[3]
                if row[4]:
                    evidence["identity"]["config_hash"] = row[4]
                evidence["manifest"] = manifest
        if "pt_reconciliation" in tables:
            row = conn.execute(
                "SELECT rec_id, result, checked_at FROM pt_reconciliation"
                " WHERE run_date=? ORDER BY rec_id DESC LIMIT 1", (trade_date,),
            ).fetchone()
            if row:
                evidence["reconciliation"] = {
                    "rec_id": row[0], "result": row[1], "checked_at": row[2],
                }
        if "audit_events" in tables:
            row = conn.execute(
                "SELECT event_id, event_hash, prev_hash, occurred_at FROM audit_events"
                " ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
            ).fetchone()
            if row:
                evidence["audit_chain_head"] = {
                    "event_id": row[0], "event_hash": row[1], "prev_hash": row[2],
                    "occurred_at": row[3],
                }
    return evidence


def _write_evidence(soak_dir: Path, evidence: dict) -> Path:
    """写入证据文件并嵌入自身 SHA-256（对嵌入前内容摘要）。"""
    soak_dir.mkdir(parents=True, exist_ok=True)
    inner = {k: v for k, v in evidence.items() if k != "file_self_sha256"}
    self_sha = hashlib.sha256(_canonical_json(inner).encode("utf-8")).hexdigest()
    evidence = {**inner, "file_self_sha256": self_sha}
    path = soak_dir / f"{evidence['trade_date']}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_evidence(path: Path) -> dict | None:
    """读取并自校验证据；解析失败/自哈希不符 → None（不计天）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    inner = {k: v for k, v in data.items() if k != "file_self_sha256"}
    self_sha = hashlib.sha256(_canonical_json(inner).encode("utf-8")).hexdigest()
    if data.get("file_self_sha256") != self_sha:
        return None
    return data


def _day_counts(data: dict) -> bool:
    """有效 soak 日：DAG COMPLETED + 不可变 COMPLETE manifest + 合法交易日。"""
    if not _valid_trade_date(str(data.get("trade_date") or "")):
        return False
    dag = data.get("dag") or {}
    manifest = data.get("manifest") or {}
    return (
        dag.get("status") == "COMPLETED"
        and manifest.get("status") == "COMPLETE"
        and bool(manifest.get("sha256"))
    )


def soak_status(soak_dir: str | Path) -> dict:
    """汇总 soak 证据；<5 个不同真实完成交易日 → INSUFFICIENT。"""
    soak_dir = Path(soak_dir)
    days: list[str] = []
    invalid: list[str] = []
    if soak_dir.is_dir():
        for f in sorted(soak_dir.glob("*.json")):
            data = _read_evidence(f)
            if data is None:
                invalid.append(f.name)
                continue
            if _day_counts(data):
                days.append(str(data["trade_date"]))
    days = sorted(set(days))
    ready = len(days) >= MIN_SOAK_DAYS
    return {
        "gate": "O-12",
        "completed_trade_days": days,
        "count": len(days),
        "required": MIN_SOAK_DAYS,
        "status": "PASS" if ready else "INSUFFICIENT",
        "invalid_evidence_files": sorted(set(invalid)),
        "note": "" if ready else f"不足 {MIN_SOAK_DAYS} 个不同完成交易日（不伪造等待结果）",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P8 soak 证据收集与 O-12 判定（v2）")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--soak-dir", default="runtime/v2/soak")
    parser.add_argument("--collect", default=None, help="收集指定交易日证据（默认仅汇总）")
    parser.add_argument("--code-version", default="", help="代码身份（git sha）")
    parser.add_argument("--config-hash", default="", help="配置身份")
    args = parser.parse_args(argv)

    soak_dir = Path(args.soak_dir)
    if args.collect:
        evidence = collect_day_evidence(
            args.db, args.collect,
            code_version=args.code_version, config_hash=args.config_hash,
        )
        path = _write_evidence(soak_dir, evidence)
        print(f"collected: {path}")
    status = soak_status(soak_dir)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
