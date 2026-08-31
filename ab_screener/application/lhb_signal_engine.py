"""研究型资金流评分引擎（T08）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ab_screener.domain.lhb_signal import SignalInput, evaluate_signal, recompute_from_snapshot

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "configs" / "lhb_signal_policy.yaml"


def load_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_POLICY
    return yaml.safe_load(target.read_text(encoding="utf-8"))


def run_signal(inp: SignalInput) -> dict[str, Any]:
    policy = inp.policy or load_policy()
    filled = SignalInput(**{**inp.__dict__, "policy": policy})
    return evaluate_signal(filled)


def replay_signal(snapshot: dict[str, Any]) -> dict[str, Any]:
    return recompute_from_snapshot(snapshot)
