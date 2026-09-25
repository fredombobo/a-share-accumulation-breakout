"""Read-only data qualification; never invokes a backtest or mutates a ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ab_screener.data.adapters.fundamental_statements import canonical_hash
from ab_screener.research.fundamental_factors import (
    FACTOR_IDS,
    FinancialObservation,
    evaluate_factor,
    factor_catalog,
    number,
)


def local_financial_inventory(db_path: Path) -> dict[str, Any]:
    path = db_path.resolve()
    if not path.is_file():
        raise ValueError("本地数据库不存在；体检不会创建新库")
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        inventory = {}
        for name in ("fina_indicator", "fina_indicator_history", "income", "balancesheet", "cashflow"):
            if name not in names:
                inventory[name] = {"exists": False, "rows": 0}
                continue
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
            item: dict[str, Any] = {"exists": True, "columns": columns,
                                    "rows": conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]}
            if {"ts_code", "ann_date", "available_at"} <= set(columns):
                row = conn.execute(f"SELECT COUNT(DISTINCT ts_code),MIN(ann_date),MAX(ann_date),"
                                   f"MIN(available_at),MAX(available_at) FROM {name}").fetchone()
                item.update(codes=row[0], first_announcement=row[1], last_announcement=row[2],
                            first_available_at=row[3], last_available_at=row[4])
            inventory[name] = item
    return {"db_path": str(path), "tables": inventory, "read_only": True,
            "notice": "指标表不等于三表；本清单不构成历史 PIT 面板资格证明"}


def ledger_fingerprint(db_path: Path) -> dict[str, Any]:
    """Content comparison of all legacy pt_* tables, using one read snapshot."""
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                       if str(r[0]).startswith("pt_"))
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(json.dumps(list(row), ensure_ascii=False, default=str)
                          for row in conn.execute(f"SELECT * FROM {quoted}"))
            counts[name] = len(rows)
            digest.update(name.encode())
            digest.update(json.dumps(rows, ensure_ascii=False).encode())
    return {"sha256": digest.hexdigest(), "counts": counts}


def evaluate_probe(probe: dict[str, Any], *, decision_times: list[str]) -> dict[str, Any]:
    if canonical_hash(probe["observations"]) != probe.get("observations_sha256"):
        raise ValueError("财报快照内容哈希不符，拒绝使用")
    observations = []
    for raw in probe["observations"]:
        row = dict(raw)
        row["values"] = {k: number(v) for k, v in row["values"].items()}
        observations.append(FinancialObservation(**row))
    checks = [{"ts_code": code, **asdict(evaluate_factor(observations, ts_code=code,
                                                        factor_id=factor, decision_at=at))}
              for at in decision_times for code in probe["codes"] for factor in FACTOR_IDS]
    fields = []
    for code in probe["codes"]:
        for dataset, kind in (("income", "1"), ("income", "2"), ("balancesheet", "1"), ("cashflow", "1")):
            group = [r for r in observations if r.ts_code == code and r.dataset == dataset and r.report_type == kind]
            keys = sorted({key for row in group for key in row.values})
            fields.append({"ts_code": code, "dataset": dataset, "report_type": kind, "rows": len(group),
                           "non_null_counts": {key: sum(row.values.get(key) is not None for row in group) for key in keys}})
    return {"status": "BLOCKED", "catalog": factor_catalog(), "checks": checks, "field_coverage": fields,
            "can_run_historical_experiment": False, "candidate_eligible": False,
            "blocking_reasons": ["本次抓取是当前观察快照，未提供历史原始财报与修订发布时间链",
                                 "三个标的接口体检不是冻结股票×全部决策日的覆盖率验收",
                                 "单季 EPS 还需可核验的每股口径可比性证据"],
            "next_step": "取得历史版本档案并验收专用适配器，再按预登记构建完整面板；不得倒填 available_at"}
