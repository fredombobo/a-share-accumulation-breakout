"""策略评分机器裁决：分数阶梯、防伪（工件哈希/冻结时间/文档篡改）、止损。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ab_screener.research.scorecard import evaluate, g2_from_event_study

FROZEN = "2026-10-01T09:00:00+08:00"
AFTER = "2026-12-01T09:00:00+08:00"
BEFORE = "2026-09-01T09:00:00+08:00"

G0_OK = {
    "pit_history_complete": True, "delisted_covered": True, "suspension_limit_modeled": True,
    "adjustment_complete": True, "cost_model_calibrated": True, "manifest_reproducible": True,
}
G2_OK = {"n": 900, "mean": 0.012, "ci_lo": 0.004, "placebo_mean": 0.001}
G3_OK = {
    "oos_years": 2.5, "oos_net_sharpe": 0.9, "oos_trades": 240, "includes_bear": True,
    "oos_viewed_during_development": False,
}
G4_OK = {
    "pbo": 0.05, "dsr": 0.97, "mintrl_coverage": 1.3, "perturb_20pct_sign_flip": False,
    "drop_best_5pct_still_positive": True,
}
G5_OK = {"cost_2x_expectancy": 0.004, "cost_3x_expectancy": 0.001, "capacity_multiple": 8,
         "adv_participation_cap": 0.05}
G6_OK = {"shadow_months": 13, "slippage_deviation": 0.12, "fill_rate_deviation": 0.08,
         "limit_unfillable_recorded": True}
G7_OK = {"replicated_other_source_or_regime": True, "independent_implementation_passed": True}
G8_OK = {"vol_target": True, "neutralization": True, "correlation_constraint": True,
         "drawdown_budget": True, "industry_exposure_budget": True}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_gate(root: Path, rel_dir: str, gate: str, metrics: dict, generated_at: str = AFTER) -> Path:
    folder = root / rel_dir
    folder.mkdir(parents=True, exist_ok=True)
    artifact = folder / f"{gate}.artifact.json"
    artifact.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")
    path = folder / f"{gate}.json"
    path.write_text(json.dumps({
        "gate": gate,
        "generated_at": generated_at,
        "artifacts": {str(artifact.relative_to(root)): _sha(artifact)},
        "metrics": metrics,
    }), encoding="utf-8")
    return path


def register(root: Path, repo: Path, hyp: str, *, max_trials: int = 3, trials: int = 1) -> Path:
    doc = repo / "docs" / f"PREREG-{hyp}.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(f"# {hyp}\n机制说明\n", encoding="utf-8")
    folder = root / hyp
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "registration.json").write_text(json.dumps({
        "hypothesis_id": hyp,
        "frozen_at": FROZEN,
        "mechanism": "高获利盘意味着上方供给轻",
        "document": str(doc.relative_to(repo)),
        "document_sha256": _sha(doc),
        "max_trials": max_trials,
    }), encoding="utf-8")
    (folder / "trials.json").write_text(json.dumps({"trials": [{"n": i} for i in range(trials)]}), encoding="utf-8")
    return doc


def passing_hypothesis(root: Path, repo: Path, hyp: str) -> None:
    register(root, repo, hyp)
    write_gate(root, hyp, "G2", G2_OK)
    write_gate(root, hyp, "G3", G3_OK)
    write_gate(root, hyp, "G4", G4_OK)


def gate_status(result: dict, hyp: str, gate: str) -> str:
    h = next(x for x in result["hypotheses"] if x["hypothesis_id"] == hyp)
    return next(g["status"] for g in h["gates"] if g["gate"] == gate)


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    root, repo = tmp_path / "evidence", tmp_path / "repo"
    root.mkdir()
    repo.mkdir()
    return root, repo


def test_empty_evidence_scores_two(dirs) -> None:
    root, repo = dirs
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 2
    assert result["system"]["G0"]["status"] == "MISSING"


def test_g0_pass_scores_four_and_names_next_blocker(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 4
    assert result["next_blockers"] == ["没有任何预登记假设通过 G1–G4"]


def test_g0_missing_capability_fails(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", {**G0_OK, "pit_history_complete": False})
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 2
    assert "全历史 PIT" in result["next_blockers"][0]


def test_tampered_artifact_invalidates_gate(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    (root / "system" / "G0.artifact.json").write_text("{}", encoding="utf-8")
    result = evaluate(root, repo_root=repo)
    assert result["system"]["G0"]["status"] == "INVALID"
    assert result["score"] == 2


def test_gate_without_artifacts_is_invalid(dirs) -> None:
    root, repo = dirs
    (root / "system").mkdir()
    (root / "system" / "G0.json").write_text(json.dumps(
        {"gate": "G0", "generated_at": AFTER, "artifacts": {}, "metrics": G0_OK}), encoding="utf-8")
    assert evaluate(root, repo_root=repo)["system"]["G0"]["status"] == "INVALID"


def test_artifact_path_outside_root_is_invalid(dirs, tmp_path: Path) -> None:
    root, repo = dirs
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / "system").mkdir()
    (root / "system" / "G0.json").write_text(json.dumps({
        "gate": "G0", "generated_at": AFTER,
        "artifacts": {"../outside.json": _sha(outside)}, "metrics": G0_OK,
    }), encoding="utf-8")
    result = evaluate(root, repo_root=repo)
    assert result["system"]["G0"]["status"] == "INVALID"
    assert any("越界" in r for r in result["system"]["G0"]["reasons"])


def test_single_mechanism_passing_scores_six(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 6
    assert result["hypotheses"][0]["status"] == "SINGLE_MECHANISM_PASS"
    assert result["next_blockers"] == ["缺少组合层证据 portfolio/portfolio.json"]


def test_evidence_generated_before_freeze_is_invalid(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    write_gate(root, "H-A", "G3", G3_OK, generated_at=BEFORE)
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", "G3") == "INVALID"
    assert result["score"] == 4


def test_prereg_document_edited_after_freeze_is_invalid(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    (repo / "docs" / "PREREG-H-A.md").write_text("# 事后改了阈值\n", encoding="utf-8")
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", "G1") == "INVALID"
    assert result["score"] == 4


def test_trial_budget_overrun_kills_hypothesis(dirs) -> None:
    root, repo = dirs
    register(root, repo, "H-A", max_trials=2, trials=3)
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", "G1") == "KILLED"
    assert result["hypotheses"][0]["status"] == "KILLED"


def test_max_trials_above_cap_is_invalid(dirs) -> None:
    root, repo = dirs
    register(root, repo, "H-A", max_trials=9)
    assert gate_status(evaluate(root, repo_root=repo), "H-A", "G1") == "INVALID"


@pytest.mark.parametrize(("gate", "metrics"), [
    ("G2", {"n": 900, "mean": -0.001, "ci_lo": -0.004, "placebo_mean": 0.0}),
    ("G3", {**G3_OK, "oos_net_sharpe": 0.2}),
    ("G3", {**G3_OK, "oos_trades": 60}),
    ("G4", {**G4_OK, "pbo": 0.3125}),
    ("G4", {**G4_OK, "perturb_20pct_sign_flip": True}),
])
def test_kill_criteria(dirs, gate: str, metrics: dict) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    write_gate(root, "H-A", gate, metrics)
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", gate) == "KILLED"
    assert result["hypotheses"][0]["killed_by"] == [gate]
    assert result["score"] == 4


def test_g4_near_misses_fail_without_kill(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    write_gate(root, "H-A", "G4", {**G4_OK, "pbo": 0.15, "dsr": 0.90})
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", "G4") == "FAIL"
    assert result["hypotheses"][0]["status"] == "OPEN"


def _portfolio(root: Path, **overrides) -> None:
    spec = {
        "members": ["H-A", "H-B"], "max_pairwise_correlation": 0.3, "portfolio_net_sharpe": 1.0,
        "portfolio_cost_3x_expectancy": 0.001, "forward_live_months": 13, "decay_detected": False,
    }
    spec.update(overrides)
    (root / "portfolio").mkdir(exist_ok=True)
    (root / "portfolio" / "portfolio.json").write_text(json.dumps(spec), encoding="utf-8")
    for gate, metrics in (("G5", G5_OK), ("G6", G6_OK), ("G7", G7_OK), ("G8", G8_OK)):
        write_gate(root, "portfolio", gate, metrics)


def test_full_ladder_reaches_nine(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    passing_hypothesis(root, repo, "H-B")
    _portfolio(root)
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 9
    assert result["next_blockers"] == []


def test_short_forward_record_caps_at_eight(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    passing_hypothesis(root, repo, "H-B")
    _portfolio(root, forward_live_months=7)
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 8
    assert "前瞻一致记录不足 12 个月" in result["next_blockers"]


def test_portfolio_needs_two_six_point_members(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    _portfolio(root)
    result = evaluate(root, repo_root=repo)
    assert result["score"] == 6
    assert any("未达 6 分" in b for b in result["next_blockers"])


def test_correlated_members_block_eight(dirs) -> None:
    root, repo = dirs
    write_gate(root, "system", "G0", G0_OK)
    passing_hypothesis(root, repo, "H-A")
    passing_hypothesis(root, repo, "H-B")
    _portfolio(root, max_pairwise_correlation=0.8)
    assert evaluate(root, repo_root=repo)["score"] == 6


def test_g2_import_reproduces_phase1_no_edge(dirs) -> None:
    """用 Phase 1 真实 H20 数字：点估计为正但 CI 跨 0 → FAIL（非 KILLED）。"""
    root, repo = dirs
    study = root / "H-A" / "event-study"
    study.mkdir(parents=True)
    (study / "manifest.json").write_text(json.dumps(
        {"generated_at": AFTER, "verdict": "NO_CONDITIONAL_EDGE"}), encoding="utf-8")
    (study / "stats.json").write_text(json.dumps({
        "event_diff": {"20": {"n": 2985, "mean": 0.0048, "lo": -0.0012, "hi": 0.0105}},
        "placebo_diff": {"20": {"mean": -0.0013}},
    }), encoding="utf-8")
    evidence = g2_from_event_study(study, root)
    assert set(evidence["artifacts"]) == {"H-A/event-study/manifest.json", "H-A/event-study/stats.json"}
    register(root, repo, "H-A")
    (root / "H-A" / "G2.json").write_text(json.dumps(evidence), encoding="utf-8")
    write_gate(root, "system", "G0", G0_OK)
    result = evaluate(root, repo_root=repo)
    assert gate_status(result, "H-A", "G2") == "FAIL"
    assert result["score"] == 4


def test_cli_refuses_to_overwrite_existing_g2(dirs, capsys) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import strategy_scorecard

    root, _repo = dirs
    (root / "H-A").mkdir()
    (root / "H-A" / "G2.json").write_text("{}", encoding="utf-8")
    code = strategy_scorecard.main(
        ["--root", str(root), "--import-g2", str(root / "x"), "--hypothesis", "H-A"]
    )
    assert code == 2
    assert "拒绝覆盖" in capsys.readouterr().out
    assert strategy_scorecard.main(["--root", str(root / "empty")]) == 0
    assert "SCORE=2/10" in capsys.readouterr().out
