"""G0 数据与测量门禁审计：从真实数据库算出六项指标并生成可复核证据。

只读数据库（`mode=ro` + `query_only`）；产物写到证据根 `system/g0-<时间戳>/`，
再生成 `system/G0.json`（列出每个工件的 SHA-256），供 `ab_screener/research/scorecard.py` 复核。

六项指标（STRATEGY-SCORECARD-V0 G0）：
1. pit_history_complete —— 协议所需数据集在 ADR-022 rule-v1 下都有可用时点口径；
2. delisted_covered —— 窗口内退市股有行情且最后一根 K 线距退市日 ≤ 30 天，覆盖率 ≥ 95%；
3. suspension_limit_modeled —— 执行模型与一字板/停牌退出回归测试存在（记录其哈希）；
4. adjustment_complete —— 窗口内 pct_chg/pre_close 非空率 ≥ 99.9%，且与 close/pre_close 一致率 ≥ 99.9%；
5. cost_model_calibrated —— 成本配置来源为券商对账单，且对账单文件哈希一致；
6. manifest_reproducible —— 随机抽样的 daily 分区指纹重算全部一致。
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.research.availability_policy import POLICY_VERSION, AvailabilityPolicyError, classify

_TZ = ZoneInfo("Asia/Shanghai")
_ROOT = Path(__file__).resolve().parents[2]

PROTOCOL_DATASETS = ("daily", "daily_basic", "stock_basic", "delisted_basic")
EXECUTION_EVIDENCE = (
    "ab_screener/domain/execution/models.py",
    "tests/test_execution_corrections_20260913.py",
    "tests/test_limit_up_ladder.py",
    "tests/test_suspended_open_exit.py",
)
DELISTED_MIN_COVERAGE = 0.95
DELISTED_MAX_GAP_DAYS = 30
ADJ_MIN_RATE = 0.999


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA query_only=1")
    return conn


def check_pit_policy() -> dict[str, Any]:
    per: dict[str, str] = {}
    ok = True
    for dataset in PROTOCOL_DATASETS:
        try:
            per[dataset] = classify(dataset, None)
        except AvailabilityPolicyError as exc:
            per[dataset] = f"BLOCKED: {exc}"
            ok = False
    return {"policy": POLICY_VERSION, "datasets": per, "pass": ok}


def check_delisted(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT ts_code, list_date, delist_date FROM delisted_basic"
        " WHERE delist_date>=? AND delist_date<=? AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')",
        (start, end),
    ).fetchall()
    missing: list[str] = []
    stale: list[str] = []
    for code, _list_date, delist in rows:
        last = conn.execute("SELECT MAX(trade_date) FROM daily WHERE ts_code=?", (code,)).fetchone()[0]
        if not last:
            missing.append(code)
            continue
        gap = (datetime.strptime(str(delist), "%Y%m%d") - datetime.strptime(str(last), "%Y%m%d")).days
        if gap > DELISTED_MAX_GAP_DAYS:
            stale.append(code)
    total = len(rows)
    covered = total - len(missing) - len(stale)
    rate = covered / total if total else 0.0
    return {
        "window": [start, end],
        "delisted_in_window": total,
        "covered": covered,
        "no_daily": len(missing),
        "stale_last_bar": len(stale),
        "examples_no_daily": missing[:20],
        "examples_stale": stale[:20],
        "coverage": round(rate, 6),
        "pass": total > 0 and rate >= DELISTED_MIN_COVERAGE,
    }


def check_execution_model(repo_root: Path) -> dict[str, Any]:
    files = {rel: (_sha_file(repo_root / rel) if (repo_root / rel).is_file() else None) for rel in EXECUTION_EVIDENCE}
    return {"files": files, "pass": all(files.values())}


def check_adjustment(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    total, non_null, consistent = conn.execute(
        "SELECT COUNT(*),"
        " SUM(CASE WHEN pct_chg IS NOT NULL AND pre_close IS NOT NULL AND pre_close>0 THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN pct_chg IS NOT NULL AND pre_close>0 AND close IS NOT NULL"
        "      AND ABS((close/pre_close-1)*100 - pct_chg) < 0.05 THEN 1 ELSE 0 END)"
        " FROM daily WHERE trade_date>=? AND trade_date<=?",
        (start, end),
    ).fetchone()
    total = int(total or 0)
    non_null_rate = (non_null or 0) / total if total else 0.0
    consistent_rate = (consistent or 0) / total if total else 0.0
    return {
        "window": [start, end],
        "rows": total,
        "non_null_rate": round(non_null_rate, 6),
        "consistent_rate": round(consistent_rate, 6),
        "pass": total > 0 and non_null_rate >= ADJ_MIN_RATE and consistent_rate >= ADJ_MIN_RATE,
    }


def check_cost_model(cost_path: Path, evidence_root: Path) -> dict[str, Any]:
    cfg = json.loads(cost_path.read_text(encoding="utf-8"))
    reasons: list[str] = []
    if cfg.get("calibration_source") != "broker_statement":
        reasons.append("成本参数为假设值（calibration_source != broker_statement）")
    statement = cfg.get("statement_path")
    if statement:
        target = (evidence_root / statement).resolve()
        if not target.is_relative_to(evidence_root.resolve()) or not target.is_file():
            reasons.append("对账单文件不在证据根内或不存在")
        elif _sha_file(target) != cfg.get("statement_sha256"):
            reasons.append("对账单 SHA-256 不符")
    elif cfg.get("calibration_source") == "broker_statement":
        reasons.append("缺少 statement_path")
    return {"config": cfg, "config_sha256": _sha_file(cost_path), "reasons": reasons, "pass": not reasons}


def _day_hash(conn: sqlite3.Connection, trade_date: str) -> str:
    """与 MarketRepository.compute_daily_day_hash 相同的算法（只读复算）。"""
    rows = conn.execute(
        "SELECT ts_code, open, high, low, close, vol FROM daily WHERE trade_date=? ORDER BY ts_code",
        (trade_date,),
    ).fetchall()
    digest = hashlib.sha256()
    for r in rows:
        digest.update(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}\n".encode())
    return digest.hexdigest()


def check_manifest(conn: sqlite3.Connection, start: str, end: str, *, sample: int, seed: int) -> dict[str, Any]:
    try:
        stored = conn.execute(
            "SELECT trade_date, content_sha256 FROM dataset_partitions"
            " WHERE dataset='daily' AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (start, end),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"pass": False, "reason": "缺少 dataset_partitions 表"}
    days = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=?", (start, end)
    ).fetchall()}
    picked = random.Random(seed).sample(stored, min(sample, len(stored)))
    mismatches = [str(d) for d, sha in picked if _day_hash(conn, str(d)) != sha]
    registered = len({str(r[0]) for r in stored} & days)
    coverage = registered / len(days) if days else 0.0
    return {
        "window": [start, end],
        "trading_days": len(days),
        "registered_partitions": registered,
        "registration_coverage": round(coverage, 6),
        "sampled": [str(d) for d, _ in picked],
        "mismatches": mismatches,
        "seed": seed,
        "pass": bool(picked) and not mismatches and coverage >= 0.99,
    }


def run_g0_audit(
    db_path: Path,
    evidence_root: Path,
    *,
    start: str = "20150101",
    end: str | None = None,
    sample: int = 40,
    seed: int = 20260925,
    cost_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """运行审计，写工件与 system/G0.json，返回 G0 证据。"""
    db = Path(db_path).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"数据库不存在: {db}")
    root = Path(evidence_root).resolve()
    repo = (repo_root or _ROOT).resolve()
    cost = (cost_path or repo / "configs" / "research" / "cost_model_v1.json").resolve()
    stamp = datetime.now(_TZ).strftime("%Y%m%dT%H%M%S")
    out = root / "system" / f"g0-{stamp}"
    out.mkdir(parents=True, exist_ok=False)

    with closing(_ro(db)) as conn:
        window_end = end or str(conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0] or start)
        reports = {
            "pit_policy": check_pit_policy(),
            "delisted_coverage": check_delisted(conn, start, window_end),
            "execution_model": check_execution_model(repo),
            "adjustment": check_adjustment(conn, start, window_end),
            "cost_model": check_cost_model(cost, root),
            "manifest_repro": check_manifest(conn, start, window_end, sample=sample, seed=seed),
        }
    artifacts: dict[str, str] = {}
    for name, report in reports.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts[str(path.relative_to(root))] = _sha_file(path)
    evidence = {
        "gate": "G0",
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "source": "ab_screener/data/g0_audit.py",
        "db": str(db),
        "window": [start, window_end],
        "artifacts": artifacts,
        "metrics": {
            "pit_history_complete": reports["pit_policy"]["pass"],
            "delisted_covered": reports["delisted_coverage"]["pass"],
            "suspension_limit_modeled": reports["execution_model"]["pass"],
            "adjustment_complete": reports["adjustment"]["pass"],
            "cost_model_calibrated": reports["cost_model"]["pass"],
            "manifest_reproducible": reports["manifest_repro"]["pass"],
        },
    }
    (root / "system" / "G0.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence
