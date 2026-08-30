"""Application service closing professional backtests into daily scan profiles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ab_screener.data.strategy_profile_repository import (
    StrategyProfileRepository,
    StrategyProfileRepositoryError,
)
from ab_screener.domain.profile import StrategyProfile
from ab_screener.research.pit_reader import ResearchPitError, latest_research_cutoff
from ab_screener.research.portfolio_metric_contract import portfolio_total_return
from ab_screener.research.professional_grid import (
    ProfessionalGridError,
    validate_fixed_parameters,
)
from build_version import build_version

PROFILE_ID = "professional-backtest-daily-scan"
PROFILE_SCHEMA_VERSION = 4
MANUAL_PROFILE_ID = "manual-daily-research-scan"
PROFILE_BOUNDARY = {
    "scope": "DAILY_A_POOL_TECHNICAL_ENTRY",
    "manual_activation_required": True,
    "automatic_promotion": False,
    "b_pool_uses_profile": False,
    "allowed_sources": ["BUILT_IN", "MANUAL_RESEARCH", "PROFESSIONAL_BACKTEST"],
    "daily_extra_gates": [
        "数据新鲜度",
        "市场环境",
        "资金流",
        "基本面",
        "流动性与评分",
    ],
    "notice": (
        "用户可独立选择系统默认、手工研究参数或合格回测档案。档案只统一 A 池"
        "技术入场和风险参考；今日扫描仍叠加数据与质量门禁，B 池继续使用固定"
        "宽松观察规则。本平台只用于研究学习，不构成荐股或买入指令。"
    ),
}


class ProfileActivationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _check(code: str, label: str, passed: bool, message: str) -> dict[str, Any]:
    return {"code": code, "label": label, "passed": bool(passed), "message": message}


def _profile_public(record: dict[str, Any]) -> dict[str, Any]:
    profile: StrategyProfile = record["profile"]
    return {
        "profile_id": profile.profile_id,
        "name": profile.name,
        "version": profile.version,
        "schema_version": profile.schema_version,
        "is_default": profile.is_default,
        "status": "active" if record["storage_status"] in {"active", "built_in"} else record["storage_status"],
        "storage_status": record["storage_status"],
        "config_hash": record["config_hash"],
        "activated_at": record.get("created_at"),
        "entry": profile.signal_kwargs(),
        "exit_reference": profile.exit_params(),
        "required_scan_days": profile.required_scan_days(),
        "source": {
            "kind": profile.source_kind,
            "task_id": profile.source_task_id,
            "param_id": profile.source_param_id,
            "code_version": profile.source_code_version,
            "dataset_version": profile.source_dataset_version,
            "input_hash": profile.source_input_hash,
            "evidence": profile.source_evidence,
        },
        "notes": profile.notes,
    }


def profile_state(db_path: str | Path, *, history_limit: int = 10) -> dict[str, Any]:
    repo = StrategyProfileRepository(db_path)
    effective = repo.effective()
    history = [_profile_public(item) for item in repo.history(history_limit)]
    return {
        "active": _profile_public(effective),
        "history": history,
        "boundary": PROFILE_BOUNDARY,
        "live_trading_enabled": False,
    }


def _already_active(repo: StrategyProfileRepository, task_id: str) -> bool:
    record = repo.effective()
    profile: StrategyProfile = record["profile"]
    return not profile.is_default and profile.source_task_id == task_id


def activation_status(db_path: str | Path, task: dict[str, Any] | None) -> dict[str, Any]:
    repo = StrategyProfileRepository(db_path)
    task_id = str((task or {}).get("task_id") or (task or {}).get("research_run_id") or "")
    if task_id and _already_active(repo, task_id):
        return {
            "task_id": task_id,
            "can_activate": True,
            "already_active": True,
            "checks": [],
            "reasons": [],
            "boundary": PROFILE_BOUNDARY,
        }

    payload = task or {}
    checks: list[dict[str, Any]] = []
    status_ok = bool(payload) and payload.get("status") == "done"
    checks.append(_check(
        "TASK_DONE", "回测已完成", status_ok,
        "回测已完成" if status_ok else "请先等待专业回测完整结束",
    ))
    mode_ok = bool(payload) and payload.get("research_mode") == "professional_grid"
    checks.append(_check(
        "PROFESSIONAL_GRID", "专业网格口径", mode_ok,
        "使用专业网格回测" if mode_ok else "只有专业网格回测可生成每日扫描档案",
    ))
    result = payload.get("result") or {}
    selected_ok = isinstance(result.get("selected"), dict)
    checks.append(_check(
        "PARAMETERS_SELECTED", "参数完整", selected_ok,
        "已有冻结的入场与退出参数" if selected_ok else "本次没有可用的入选参数",
    ))
    verdict = str(result.get("verdict") or payload.get("verdict") or "")
    verdict_ok = verdict == "EXPLORATORY_PROMISING"
    checks.append(_check(
        "EVIDENCE_PROMISING", "OOS/WF/成本证据", verdict_ok,
        "探索证据达到候选档案门槛" if verdict_ok else "OOS、滚动窗口、基线或成本压力尚未全部通过",
    ))
    current_code = str(build_version())
    task_code = str(payload.get("code_version") or "")
    code_ok = bool(task_code) and task_code == current_code
    checks.append(_check(
        "CODE_IDENTITY_CURRENT", "代码版本一致", code_ok,
        "代码身份仍有效" if code_ok else "系统代码已变化，请用当前版本重新回测",
    ))
    current_dataset = ""
    dataset_error = ""
    try:
        current_dataset = latest_research_cutoff(db_path)
    except ResearchPitError as exc:
        dataset_error = str(exc)
    task_dataset = str(payload.get("dataset_version") or "")
    dataset_ok = bool(task_dataset) and bool(current_dataset) and task_dataset == current_dataset
    checks.append(_check(
        "DATASET_IDENTITY_CURRENT", "数据时点一致", dataset_ok,
        "数据知识边界仍有效" if dataset_ok else (
            f"无法验证当前 PIT 数据：{dataset_error}" if dataset_error
            else "本地研究数据已变化，请重新回测后再启用"
        ),
    ))
    reasons = [item for item in checks if not item["passed"]]
    return {
        "task_id": task_id or None,
        "can_activate": bool(task_id) and not reasons,
        "already_active": False,
        "checks": checks,
        "reasons": reasons,
        "boundary": PROFILE_BOUNDARY,
    }


def _compact_evidence(result: dict[str, Any]) -> dict[str, Any]:
    selected = result.get("selected") or {}
    cost_metrics = (result.get("cost_stress") or {}).get("metrics") or {}
    wf = result.get("wf") or {}
    return {
        "verdict": result.get("verdict"),
        "verdict_label": result.get("verdict_label"),
        "oos": selected.get("oos") or {},
        "wf_pass": bool(wf.get("wf_pass")),
        "wf_evidence_complete": bool(wf.get("evidence_complete")),
        "cost_2x_portfolio_total_return": portfolio_total_return(cost_metrics),
        "baseline_portfolio_total_returns": {
            key: portfolio_total_return(value)
            for key, value in (result.get("baselines") or {}).items()
            if isinstance(value, dict)
        },
    }


def _profile_from_task(task: dict[str, Any]) -> StrategyProfile:
    result = task.get("result") or {}
    selected = result.get("selected") or {}
    signal = selected.get("signal") or {}
    exit_params = selected.get("exit") or {}
    try:
        return StrategyProfile(
            profile_id=PROFILE_ID,
            name="专业回测人工启用档案",
            schema_version=PROFILE_SCHEMA_VERSION,
            version=f"{task['task_id']}:profile-v3",
            status="active",
            box_min_days=int(signal["box_min_days"]),
            box_max_days=int(signal["box_max_days"]),
            box_max_amp=float(signal["box_max_amp"]),
            breakout_vol_ratio=float(signal["breakout_vol_ratio"]),
            breakout_chg_min=float(signal["breakout_chg_min"]),
            breakout_chg_max=float(signal["breakout_chg_max"]),
            breakout_vs_recent_vol_ratio=float(signal["breakout_vs_recent_vol_ratio"]),
            breakout_window_days=int(signal["breakout_window_days"]),
            require_structure=bool(signal["require_structure"]),
            vol_ratio_min=float(exit_params["vol_ratio_min"]),
            strong_reset=int(exit_params["strong_reset"]),
            exit_window=int(exit_params["exit_window"]),
            stop_pct=float(exit_params["stop_pct"]),
            target_pct=float(exit_params.get("target_pct", 0.12)),
            max_hold_days=int(exit_params.get("max_hold_days", 30)),
            notes=[
                "仅用于每日 A 池技术入场参数；B 池与额外生产门禁不随档案变化。",
                "这是人工启用的探索性候选档案，不代表正式研究晋级或收益承诺。",
            ],
            source_kind="PROFESSIONAL_BACKTEST",
            source_task_id=str(task["task_id"]),
            source_param_id=str(selected.get("param_id") or ""),
            source_code_version=str(task.get("code_version") or ""),
            source_dataset_version=str(task.get("dataset_version") or ""),
            source_input_hash=str(task.get("input_hash") or ""),
            source_evidence=_compact_evidence(result),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileActivationError(
            "BACKTEST_PARAMETERS_INVALID",
            "回测入选参数不完整或越界，不能生成每日扫描档案",
        ) from exc


def activate_from_task(
    db_path: str | Path,
    task: dict[str, Any],
    *,
    acknowledge_exploratory: bool,
) -> dict[str, Any]:
    if not acknowledge_exploratory:
        raise ProfileActivationError(
            "PROFILE_ACKNOWLEDGEMENT_REQUIRED",
            "请先确认：这只是探索性每日选股参数，不是收益承诺或自动晋级",
        )
    eligibility = activation_status(db_path, task)
    if eligibility["already_active"]:
        return {**profile_state(db_path), "activation": eligibility, "idempotent": True}
    if not eligibility["can_activate"]:
        raise ProfileActivationError(
            "BACKTEST_PROFILE_NOT_ELIGIBLE",
            "该回测结果还不能用于今日选股",
            {"checks": eligibility["checks"]},
        )
    profile = _profile_from_task(task)
    try:
        StrategyProfileRepository(db_path).activate(profile)
    except StrategyProfileRepositoryError as exc:
        raise ProfileActivationError(exc.code, str(exc), exc.details) from exc
    return {
        **profile_state(db_path),
        "activation": activation_status(db_path, task),
        "idempotent": False,
    }


def activate_manual_profile(
    db_path: str | Path,
    parameters: dict[str, Any],
    *,
    acknowledge_research_only: bool,
) -> dict[str, Any]:
    """Activate one user-entered research profile without claiming backtest evidence."""
    if not acknowledge_research_only:
        raise ProfileActivationError(
            "MANUAL_PROFILE_ACKNOWLEDGEMENT_REQUIRED",
            "请先确认：手工参数未经回测验证，只用于个人研究学习",
        )
    try:
        normalized = validate_fixed_parameters(parameters)
    except ProfessionalGridError as exc:
        raise ProfileActivationError(exc.code, str(exc), exc.details) from exc

    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parameter_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    code_version = str(build_version())
    version_seed = f"{PROFILE_SCHEMA_VERSION}:{parameter_hash}:{code_version}"
    version_hash = hashlib.sha256(version_seed.encode("utf-8")).hexdigest()[:16]
    signal = normalized["signal"]
    exit_params = normalized["exit"]
    profile = StrategyProfile(
        profile_id=MANUAL_PROFILE_ID,
        name="用户手工研究参数",
        schema_version=PROFILE_SCHEMA_VERSION,
        version=f"manual-{version_hash}",
        status="active",
        box_min_days=int(signal["box_min_days"]),
        box_max_days=int(signal["box_max_days"]),
        box_max_amp=float(signal["box_max_amp"]),
        breakout_vol_ratio=float(signal["breakout_vol_ratio"]),
        breakout_chg_min=float(signal["breakout_chg_min"]),
        breakout_chg_max=float(signal["breakout_chg_max"]),
        breakout_vs_recent_vol_ratio=float(signal["breakout_vs_recent_vol_ratio"]),
        breakout_window_days=int(signal["breakout_window_days"]),
        require_structure=bool(signal["require_structure"]),
        vol_ratio_min=float(exit_params["vol_ratio_min"]),
        strong_reset=int(exit_params["strong_reset"]),
        exit_window=int(exit_params["exit_window"]),
        stop_pct=float(exit_params["stop_pct"]),
        target_pct=float(exit_params["target_pct"]),
        max_hold_days=int(exit_params["max_hold_days"]),
        notes=[
            "由用户直接输入，未经过 IS/OOS、WF、基线或成本压力验证。",
            "只用于下一次 A 池研究扫描和风险参考，不构成荐股或买入指令。",
            "B 池和数据新鲜度、市场环境、资金流、基本面、流动性门禁保持不变。",
        ],
        source_kind="MANUAL_RESEARCH",
        source_code_version=code_version,
        source_input_hash=parameter_hash,
        source_evidence={
            "user_supplied": True,
            "backtest_validated": False,
            "research_only": True,
        },
    )
    repo = StrategyProfileRepository(db_path)
    effective = repo.effective()
    if effective["config_hash"] == profile.config_hash():
        return {**profile_state(db_path), "idempotent": True}
    try:
        repo.activate(profile)
    except StrategyProfileRepositoryError as exc:
        raise ProfileActivationError(exc.code, str(exc), exc.details) from exc
    return {**profile_state(db_path), "idempotent": False}


def reset_profile(db_path: str | Path, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ProfileActivationError(
            "PROFILE_RESET_CONFIRMATION_REQUIRED",
            "恢复系统默认参数前需要确认",
        )
    repo = StrategyProfileRepository(db_path)
    retired = repo.reset_to_default()
    return {**profile_state(db_path), "retired_count": retired}
