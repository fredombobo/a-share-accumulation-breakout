"""Create read-only, reproducible diagnostics for one failed research run."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.research.failure_diagnostics import diagnose_research_failure


def main() -> int:
    parser = argparse.ArgumentParser(description="复算权威研究 FAIL 的独立试验与时间稳定性")
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    checkpoint, report = _read_run_read_only(db_path, args.run_id)
    diagnostic = diagnose_research_failure(report, checkpoint)
    out = Path(args.out).resolve()
    if out.suffix.lower() == ".json":
        json_path = out
    else:
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / f"research_gate_diagnostic_{args.run_id}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = json_path.with_suffix(".md")
    markdown_path.write_text(_markdown(diagnostic), encoding="utf-8")
    print(f"[diagnostic] status={diagnostic.get('status')} class={diagnostic.get('classification')}")
    print(f"[diagnostic] json={json_path}")
    print(f"[diagnostic] markdown={markdown_path}")
    return 0


def _read_run_read_only(db_path: Path, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    uri = f"{db_path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT checkpoint_json,result_json FROM research_runs WHERE research_run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"research run not found: {run_id}")
    checkpoint = json.loads(row[0] or "{}")
    result = json.loads(row[1] or "{}")
    report = result.get("report") or result.get("trusted_report") or checkpoint.get("report") or {}
    if not isinstance(checkpoint, dict) or not isinstance(report, dict):
        raise SystemExit(f"research run payload is invalid: {run_id}")
    return checkpoint, report


def _markdown(diagnostic: dict[str, Any]) -> str:
    stats = diagnostic.get("corrected_statistics") or {}
    matrix = diagnostic.get("return_matrix") or {}
    paths = diagnostic.get("independent_is_paths") or {}
    gaps = diagnostic.get("threshold_gaps") or {}
    return "\n".join(
        [
            "# R 研究失败复算诊断",
            "",
            f"- 运行：`{diagnostic.get('research_run_id')}`",
            f"- 状态：**{diagnostic.get('status')}**",
            f"- 分类：**{diagnostic.get('classification')}**",
            f"- 工件 SHA-256：`{diagnostic.get('sha256')}`",
            "",
            "## 独立试验",
            "",
            f"- 名义参数：{matrix.get('nominal_parameters')}",
            f"- 精确独立收益路径：{matrix.get('effective_parameters')}",
            f"- IS 盈利独立路径：{paths.get('profitable')}/{paths.get('evaluated')}",
            "",
            "## 纠正后正式证据",
            "",
            f"- PBO：{stats.get('pbo')}（超门槛 {gaps.get('pbo_excess_over_0_20')}）",
            f"- DSR：{stats.get('dsr_effective_trials')}（距门槛 {gaps.get('dsr_shortfall_to_0_95')}）",
            f"- MinTRL coverage：{stats.get('min_track_record_coverage')}（距门槛 {gaps.get('mintrl_shortfall_to_1_00')}）",
            f"- 嵌套正收益窗：{stats.get('nested_positive_windows')}/{stats.get('nested_windows')}",
            "",
            "## 解释",
            "",
            str(diagnostic.get("interpretation") or diagnostic.get("reason") or "证据不足"),
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
