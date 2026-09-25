"""横截面机制 IS 检验执行器（只读数据库、受 OOS 封存约束）→ G2 证据。

用法（本机）：
  .venv312\\Scripts\\python.exe scripts\\sync_namechange.py                 # 先抓历史名称
  .venv312\\Scripts\\python.exe scripts\\run_cross_section_g2.py --hypothesis H-20260925-max-lottery
  .venv312\\Scripts\\python.exe scripts\\run_cross_section_g2.py --hypothesis H-20260925-max-lottery --variant 1

--variant 0 为主规格（写 G2.json）；1/2 为 G4 扰动（只落盘，不写 G2）。每次运行计入 trials.json，
超过预登记 max_trials 拒绝运行。只读 IS 区间，读取窗口与封存区间重叠会被 oos_seal 拒绝。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.research import cross_section as xs
from ab_screener.research.oos_seal import DEFAULT_SEAL_PATH, assert_window_allowed, seal_for

_TZ = ZoneInfo("Asia/Shanghai")
PRELOAD_START = "20150101"  # 250 日预热（协议 §6）


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(db: Path, start: str, end: str) -> dict[str, pd.DataFrame]:
    uri = f"{db.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        conn.execute("PRAGMA query_only=1")
        daily = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol FROM daily"
            " WHERE trade_date>=? AND trade_date<=?", conn, params=[start, end],
        )
        basic = pd.read_sql_query(
            "SELECT ts_code, trade_date, turnover_rate, pe, total_mv FROM daily_basic"
            " WHERE trade_date>=? AND trade_date<=?", conn, params=[start, end],
        )
        listed = pd.read_sql_query("SELECT ts_code, list_date FROM stock_basic", conn)
        delisted = pd.read_sql_query("SELECT ts_code, list_date, delist_date FROM delisted_basic", conn)
    listing = pd.concat([listed.assign(delist_date=None), delisted], ignore_index=True)
    listing = listing.drop_duplicates("ts_code", keep="last")
    return {"daily": daily, "basic": basic, "listing": listing}


def load_names(reference: Path) -> tuple[pd.DataFrame, str]:
    csv, meta_path = reference / "namechange.csv", reference / "namechange.meta.json"
    if not csv.is_file() or not meta_path.is_file():
        raise FileNotFoundError("缺少历史名称参考（先运行 scripts/sync_namechange.py）；不退回当前名称")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    digest = _sha(csv)
    if digest != meta.get("sha256"):
        raise ValueError("namechange.csv 与 meta 记录的 SHA-256 不符")
    names = pd.read_csv(csv, dtype=str)
    return names, digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="横截面机制 IS 检验 → G2 证据")
    parser.add_argument("--hypothesis", required=True, choices=sorted(xs.PRIMARY_SPECS))
    parser.add_argument("--variant", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--root", default="runtime/research/scorecard")
    parser.add_argument("--reference", default="runtime/research/reference")
    parser.add_argument("--cost", default=str(ROOT / "configs" / "research" / "cost_model_v1.json"))
    args = parser.parse_args(argv)

    hyp = args.hypothesis
    folder = Path(args.root).resolve() / hyp
    reg_path = folder / "registration.json"
    if not reg_path.is_file():
        print(f"REFUSED: 未预登记（先运行 strategy_scorecard.py --register docs/prereg/{hyp}.md）")
        return 2
    registration = json.loads(reg_path.read_text(encoding="utf-8"))
    trials_path = folder / "trials.json"
    trials = json.loads(trials_path.read_text(encoding="utf-8")).get("trials", []) if trials_path.is_file() else []
    if len(trials) >= int(registration["max_trials"]):
        print(f"REFUSED: 试验次数已达上限 {registration['max_trials']}")
        return 2
    if any(t.get("variant") == args.variant for t in trials):
        print(f"REFUSED: variant {args.variant} 已运行过，不得重跑")
        return 2
    if args.variant == 0 and (folder / "G2.json").exists():
        print("REFUSED: G2.json 已存在")
        return 2

    seal = seal_for(hyp, DEFAULT_SEAL_PATH)
    if seal is None:
        print("REFUSED: 假设不在封存配置中")
        return 2
    is_start, is_end = seal["in_sample"]
    assert_window_allowed(hyp, PRELOAD_START, is_end)

    spec = xs.PRIMARY_SPECS[hyp] if args.variant == 0 else xs.PERTURBATIONS[hyp][args.variant - 1]
    names, names_sha = load_names(Path(args.reference))
    cost = json.loads(Path(args.cost).read_text(encoding="utf-8"))
    started = datetime.now(_TZ).isoformat(timespec="seconds")
    inputs = load_inputs(Path(args.db), PRELOAD_START, is_end)
    panel = xs.build_panel(inputs["daily"], inputs["basic"])
    series = xs.monthly_series(panel, spec, inputs["listing"], names, start=is_start, end=is_end, cost=cost)
    stats = xs.g2_statistics(series)

    trial_id = f"is-v{args.variant}-{datetime.now(_TZ).strftime('%Y%m%dT%H%M%S')}"
    out = folder / trial_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "hypothesis_id": hyp,
        "trial_id": trial_id,
        "variant": args.variant,
        "spec": asdict(spec),
        "window": {"in_sample": [is_start, is_end], "preload_start": PRELOAD_START},
        "started_at": started,
        "generated_at": datetime.now(_TZ).isoformat(timespec="seconds"),
        "db": str(Path(args.db).resolve()),
        "rows": {k: len(v) for k, v in inputs.items()},
        "inputs_sha256": {
            "namechange.csv": names_sha,
            "cost_model": _sha(Path(args.cost)),
            "registration.json": _sha(reg_path),
            "oos_seal.json": _sha(DEFAULT_SEAL_PATH),
        },
        "code_sha256": {
            "ab_screener/research/cross_section.py": _sha(ROOT / "ab_screener/research/cross_section.py"),
            "scripts/run_cross_section_g2.py": _sha(Path(__file__).resolve()),
        },
        "availability_basis": "RULE_DERIVED (rule-v1)",
    }
    files = {
        "manifest.json": manifest,
        "months.json": series["months"],
        "placebo.json": [float(v) for v in series["placebo_gross_means"]],
        "stats.json": stats,
    }
    for name, payload in files.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    trials.append({"trial_id": trial_id, "variant": args.variant, "spec": asdict(spec),
                   "generated_at": manifest["generated_at"], "mean_net": stats["mean"]})
    trials_path.write_text(json.dumps({"trials": trials}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.variant == 0:
        root = Path(args.root).resolve()
        evidence = {
            "gate": "G2",
            "generated_at": manifest["generated_at"],
            "source": "scripts/run_cross_section_g2.py",
            "trial_id": trial_id,
            "artifacts": {str((out / n).relative_to(root)): _sha(out / n) for n in files},
            "metrics": {
                "n": stats["n"],
                "mean": stats["mean"],
                "ci_lo": stats["ci_lo"],
                "ci_hi": stats["ci_hi"],
                "gross_mean": stats["gross_mean"],
                "placebo_threshold": stats["placebo_gross_p95"],
                "placebo_mean": stats["placebo_mean"],
            },
        }
        (folder / "G2.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    verdict = "IS_POSITIVE" if stats["ci_lo"] > 0 else ("FAIL_IS_NONPOSITIVE" if stats["mean"] <= 0 else "INCONCLUSIVE")
    print(f"{hyp} variant={args.variant} verdict={verdict}（最终以 strategy_scorecard.py 为准）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
