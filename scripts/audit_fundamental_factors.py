"""Qualify preregistered accounting factors without writing the production DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.data.adapters.fundamental_statements import canonical_hash, fetch_statement_probe
from ab_screener.research.fundamental_audit import (
    evaluate_probe,
    ledger_fingerprint,
    local_financial_inventory,
)
from ab_screener.research.fundamental_factors import factor_catalog
from build_version import build_version

PREREG = ROOT / "docs/FUNDAMENTAL-FACTORS-PREREGISTRATION-2026-09-05.md"
PREREG_SHA256 = "78f276171513c95c21cc5d80cdee337a851165c7b318fe8f2eda8b826295be47"
CODES = ["600519.SH", "000858.SZ", "600036.SH"]
DECISIONS = ["2024-02-01T15:00:00+08:00", "2025-10-15T15:00:00+08:00", "2026-09-04T15:00:00+08:00"]


def preregistration_hash() -> str:
    # Git's Windows checkout may change CRLF, but not the frozen document text.
    return hashlib.sha256(PREREG.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()


def write_new_json(path: Path, data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str, allow_nan=False)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
    return hashlib.sha256(encoded.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true", help="执行冻结的三只股票/四类报表 HTTPS 接口体检")
    mode.add_argument("--replay", type=Path, help="从带有效 manifest 的已归档 provider_probe.json 离线复核")
    parser.add_argument("--output", type=Path, help="新证据目录（必须尚不存在，不能覆盖旧报告）")
    args = parser.parse_args()
    db = ROOT / "runtime/stock_data.db"
    if ROOT.name != "accumulation_breakout":
        raise ValueError("只允许 accumulation_breakout，不操作 AETF")
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() not in {"false", "0", "off", "no", ""}:
        raise ValueError("LIVE_TRADING_ENABLED 必须保持 false")
    if preregistration_hash() != PREREG_SHA256:
        raise ValueError("预登记文档发生变化，停止体检，不允许静默改写协议")
    replay = None
    if args.replay:
        path = args.replay.resolve()
        if not path.is_relative_to((ROOT / "runtime/research").resolve()) or path.name != "provider_probe.json":
            raise ValueError("只允许本项目研究归档内的 provider_probe.json")
        manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files_sha256"].get(path.name):
            raise ValueError("归档快照哈希不匹配")
        replay = json.loads(path.read_text(encoding="utf-8"))
        if replay["codes"] != CODES or (replay["start"], replay["end"]) != ("20170101", "20260905"):
            raise ValueError("归档股票或日期与预登记不同")
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    output = (args.output or ROOT / "runtime/research" / ("fundamentals-" + now[:19].replace(":", ""))).resolve()
    # Restricted artifact scope; cannot accidentally replace source or production files.
    if not output.is_relative_to((ROOT / "runtime/research").resolve()):
        raise ValueError("证据目录必须位于本项目 runtime/research 下")
    output.mkdir(parents=True, exist_ok=False)
    baseline = ledger_fingerprint(db)
    registration = {"product": "accumulation_breakout", "code_version": build_version(),
                    "created_at": now, "preregistration_sha256": preregistration_hash(),
                    "codes": CODES, "announcement_range": ["20170101", "20260905"],
                    "historical_decisions": DECISIONS, "catalog": factor_catalog(),
                    "fetch": args.fetch, "replay_source": str(args.replay) if args.replay else None,
                    "live_trading_enabled": False}
    files = {"registration.json": write_new_json(output / "registration.json", registration)}
    inventory = local_financial_inventory(db)
    files["local_inventory.json"] = write_new_json(output / "local_inventory.json", inventory)
    if args.fetch or replay is not None:
        if replay is None:
            print("三表有限体检开始：3 只股票、串行分页、只写独立证据目录", flush=True)
            probe = fetch_statement_probe(CODES, start="20170101", end="20260905",
                                          progress=lambda message: print(message, flush=True))
        else:
            probe = replay
        files["provider_probe.json"] = write_new_json(output / "provider_probe.json", probe)
        report = evaluate_probe(probe, decision_times=[*DECISIONS, datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()])
        report["provider_status"] = probe["status"]
        report["provider_issues"] = probe["issues"]
        report["observation_count"] = len(probe["observations"])
    else:
        report = {"status": "NOT_RUN", "can_run_historical_experiment": False,
                  "message": "只检查本地；使用 --fetch 执行已登记有限真实接口体检"}
    after = ledger_fingerprint(db)
    report.update(ledger_before=baseline, ledger_after=after, ledger_unchanged=baseline == after,
                  candidate_eligible=False, return_experiment_executed=False)
    if baseline != after:
        report["status"] = "LEDGER_CHANGED_DURING_AUDIT"
    files["qualification.json"] = write_new_json(output / "qualification.json", report)
    write_new_json(output / "manifest.json", {"files_sha256": files, "registration_hash": canonical_hash(registration)})
    print(json.dumps({"status": report["status"], "report": str(output / "qualification.json"),
                      "observations": report.get("observation_count", 0),
                      "ledger_unchanged": baseline == after, "can_claim_edge": False}, ensure_ascii=False))
    return 2  # NOT_RUN / BLOCKED is never a successful historical qualification.


if __name__ == "__main__":
    raise SystemExit(main())
