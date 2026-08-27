"""生产纸面日清运维命令：DAG、风险、对账、审计锚定、备份与 soak。"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ab_screener.application.audit_service import (
    verify_audit_chain,
    verify_chain_head,
)
from ab_screener.application.platform_config import load_resolved_config
from ab_screener.application.release_evidence import current_release_identity
from ab_screener.operations.backup import create_backup
from ab_screener.operations.dag import build_eod_dag
from ab_screener.operations.scheduler import SchedulerRunner
from scripts.soak_monitor_v2 import (
    _write_evidence,
    collect_day_evidence,
    soak_status,
)

_TZ = ZoneInfo("Asia/Shanghai")


class EodOperatorError(RuntimeError):
    """日清前置条件或闭环验收失败。"""


def _latest_market_date(db_path: Path) -> str:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    value = str(row[0] or "") if row else ""
    if len(value) != 8:
        raise EodOperatorError("数据库没有有效的最新交易日")
    return value


def _scan_identity(db_path: Path, trade_date: str) -> dict[str, Any]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT run_id,git_sha,config_hash,dataset_version,input_hash,result_hash,created_at"
            " FROM scan_runs WHERE as_of=? AND status='SUCCEEDED'"
            " ORDER BY created_at DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
    if row is None:
        raise EodOperatorError(f"{trade_date} 缺少成功扫描；先完成当前版本收盘扫描")
    return {
        "run_id": row[0],
        "code_version": row[1],
        "scan_config_hash": row[2],
        "dataset_version": row[3],
        "input_hash": row[4],
        "result_hash": row[5],
        "created_at": row[6],
    }


def _load_or_create_signing_key(path: Path, *, initialize: bool) -> bytes:
    if path.is_file():
        key = path.read_bytes().strip()
        if len(key) < 32:
            raise EodOperatorError("审计签名密钥文件长度不足 32 字节")
        return key
    if not initialize:
        raise EodOperatorError(
            f"审计签名密钥不存在: {path}；首次运行需显式 --initialize-signing-key"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode("ascii")
    try:
        with path.open("xb") as handle:
            handle.write(key)
    except FileExistsError:
        return _load_or_create_signing_key(path, initialize=False)
    return key


def _input_hash(
    identity: dict[str, Any], scan: dict[str, Any], trade_date: str
) -> str:
    payload = {
        "trade_date": trade_date,
        "code_version": identity.get("code_version"),
        "platform_config_hash": identity.get("platform_config_hash"),
        "db_fingerprint": identity.get("db_fingerprint"),
        "scan": scan,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def run_eod(
    *,
    db_path: str | Path,
    trade_date: str | None,
    anchor_dir: str | Path,
    signing_key_file: str | Path,
    soak_dir: str | Path,
    backup_root: str | Path | None = None,
    initialize_signing_key: bool = False,
    create_daily_backup: bool = True,
    identity_override: dict[str, Any] | None = None,
    resolved_config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行一个最新交易日的完整纸面日清；任一环节失败即返回失败。"""
    db = Path(db_path).resolve()
    latest = _latest_market_date(db)
    selected = trade_date or latest
    if selected != latest:
        raise EodOperatorError(f"只允许日清最新交易日 {latest}，收到 {selected}")
    config = resolved_config_override or load_resolved_config()
    identity = identity_override or current_release_identity(ROOT, db)
    identity = {**identity, "platform_config_hash": config["resolved_hash"]}
    if not identity.get("worktree_clean", False) and identity_override is None:
        raise EodOperatorError("当前工作树不干净，拒绝生成生产日清证据")
    scan = _scan_identity(db, selected)
    if scan["code_version"] != identity.get("code_version"):
        raise EodOperatorError(
            "最新扫描不属于当前构建版本；请用当前服务重新扫描后再日清"
        )

    key = _load_or_create_signing_key(
        Path(signing_key_file).resolve(), initialize=initialize_signing_key
    )
    anchor_root = Path(anchor_dir).resolve()
    before_anchors = set(anchor_root.glob("audit-anchor-*.sig")) if anchor_root.is_dir() else set()
    runner = SchedulerRunner(
        str(db),
        build_eod_dag(db, today=selected),
        holder=f"eod-v2-{selected}",
        signing_key=key,
        audit_anchor_dir=str(anchor_root),
    )
    dag = runner.run_day(
        selected,
        scope_type="ACCOUNT",
        scope_id="1",
        input_hash=_input_hash(identity, scan, selected),
        today=selected,
    )
    if dag.get("status") != "COMPLETED":
        raise EodOperatorError(f"日清 DAG 未完成: {dag}")

    anchors = sorted(
        set(anchor_root.glob("audit-anchor-*.sig")) - before_anchors,
        key=lambda path: path.stat().st_mtime,
    )
    if not anchors:
        anchors = sorted(anchor_root.glob("audit-anchor-*.sig"), key=lambda p: p.stat().st_mtime)
    if not anchors:
        raise EodOperatorError("日清完成但没有生成审计外部锚点")
    anchor = anchors[-1]
    with sqlite3.connect(str(db)) as conn:
        chain = verify_audit_chain(conn)
        anchor_valid = verify_chain_head(conn, anchor, signing_key=key)
    if not chain["valid"] or not anchor_valid:
        raise EodOperatorError("审计链或外部锚点验证失败")

    evidence = collect_day_evidence(
        db,
        selected,
        code_version=str(identity.get("code_version") or ""),
        config_hash=str(identity.get("platform_config_hash") or ""),
    )
    soak_path = _write_evidence(Path(soak_dir).resolve(), evidence)
    backup: dict[str, Any] | None = None
    if create_daily_backup:
        if backup_root is None:
            raise EodOperatorError("日清要求备份，但未配置 backup_root")
        backup = create_backup(db, backup_root, compressed=True)

    return {
        "status": "PASS",
        "trade_date": selected,
        "identity": {
            "code_version": identity.get("code_version"),
            "platform_config_hash": identity.get("platform_config_hash"),
            "db_fingerprint": identity.get("db_fingerprint"),
        },
        "scan": scan,
        "dag": dag,
        "audit": {
            "events": chain["events"],
            "chain_valid": chain["valid"],
            "anchor_valid": anchor_valid,
            "anchor_path": str(anchor),
        },
        "soak_evidence": str(soak_path),
        "soak": soak_status(soak_dir),
        "backup": backup,
        "completed_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "live_trading_enabled": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Breakout v2 production paper EOD")
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date")
    parser.add_argument("--anchor-dir", required=True)
    parser.add_argument("--signing-key-file", required=True)
    parser.add_argument("--soak-dir", required=True)
    parser.add_argument("--backup-root")
    parser.add_argument("--initialize-signing-key", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    try:
        payload = run_eod(
            db_path=args.db,
            trade_date=args.trade_date,
            anchor_dir=args.anchor_dir,
            signing_key_file=args.signing_key_file,
            soak_dir=args.soak_dir,
            backup_root=args.backup_root,
            initialize_signing_key=args.initialize_signing_key,
            create_daily_backup=not args.skip_backup,
        )
    except EodOperatorError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, ensure_ascii=False))
        return 1
    if args.report:
        report = Path(args.report).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
