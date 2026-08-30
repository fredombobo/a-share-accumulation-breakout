"""Execution service for persistent professional accumulation-breakout grids."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from ab_screener.research.condition_plugins import resolve_enabled_conditions
from ab_screener.research.portfolio_metric_contract import (
    normalize_portfolio_metrics,
    portfolio_total_return,
)
from ab_screener.research.professional_grid import (
    ProfessionalGridError,
    expand_parameter_space,
    request_hash,
    resolve_universe,
)

ProgressCallback = Callable[[str, int, str], None]
CancelCheck = Callable[[], bool]


def prepare_professional_request(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, expand and freeze every mutable input before a worker starts."""
    if not isinstance(payload, dict):
        raise ProfessionalGridError("INVALID_REQUEST", "请求体必须是对象")
    strategy = str(payload.get("strategy") or "A").upper()
    if strategy != "A":
        raise ProfessionalGridError(
            "UNSUPPORTED_STRATEGY", "专业台首版只验证 A 方案横盘吸筹突破"
        )
    step = max(1, min(int(payload.get("sample_step") or 10), 60))
    max_codes = max(20, min(int(payload.get("max_codes") or 600), 1500))
    parameter_space = expand_parameter_space(payload.get("parameters"))
    universe_request = payload.get("universe") or {}
    if not isinstance(universe_request, dict):
        raise ProfessionalGridError("INVALID_UNIVERSE", "universe 必须是对象")
    universe = resolve_universe(
        db_path,
        classification=universe_request.get("classification") or "industry",
        groups=universe_request.get("groups"),
        industries=universe_request.get("industries"),
        codes=universe_request.get("codes"),
        max_codes=max_codes,
    )
    conditions = resolve_enabled_conditions(payload.get("conditions"))
    dates = _distinct_dates(db_path)
    windows = _resolve_windows(payload.get("windows"), dates)
    normalized = {
        "contract_version": "professional-backtest-v1.3.0",
        "strategy": strategy,
        "sample_step": step,
        "max_codes": max_codes,
        "parameters": parameter_space["specs"],
        "parameter_space": {
            "count": parameter_space["count"],
            "sha256": parameter_space["sha256"],
            "horizon": parameter_space["horizon"],
            "signal_group_count": len(parameter_space["signal_combinations"]),
            "exit_group_count": len(parameter_space["exit_combinations"]),
            "invalid_signal_combinations": parameter_space["invalid_signal_combinations"],
            "long_running": parameter_space["long_running"],
            "long_running_warning_combinations": parameter_space[
                "long_running_warning_combinations"
            ],
        },
        "universe": universe,
        "conditions": conditions,
        "windows": windows,
        "research_boundary": {
            "mode": "EXPLORATORY_GRID",
            "candidate_eligible": False,
            "note": "网格完成不等于研究候选；需另行预登记并通过正式晋级硬门。",
        },
    }
    normalized["input_hash"] = request_hash(normalized)
    return normalized


def execute_professional_run(
    db_path: str | Path,
    prepared_request: dict[str, Any],
    *,
    progress: ProgressCallback,
    cancel_check: CancelCheck,
) -> dict[str, Any]:
    """Run IS selection, OOS, WF, baselines and 2x cost stress."""
    from ab_screener.research.backtest_engine import run_single_backtest
    from ab_screener.research.baselines import ma_cross_baseline, random_baseline_trades
    from ab_screener.research.pit_reader import build_research_pit_snapshot
    from ab_screener.research.trusted_run import trusted_portfolio_policy
    from optimizer import ResearchCancelled, run_grid
    from walkforward import wf_recheck

    expanded = expand_parameter_space(prepared_request["parameters"])
    horizon = int(expanded["horizon"])
    windows = prepared_request["windows"]
    wf_windows = [
        (row["train_start"], row["train_end"], row["test_start"], row["test_end"])
        for row in windows.get("wf", [])
    ]
    study_starts = [windows["is"][0], windows["oos"][0]] + [row[0] for row in wf_windows]
    study_ends = [windows["is"][1], windows["oos"][1]] + [row[3] for row in wf_windows]
    progress("DATA", 2, "冻结股票池与 PIT 行情")
    snapshot = build_research_pit_snapshot(
        db_path,
        study_start=min(study_starts),
        study_end=max(study_ends),
        max_codes=len(prepared_request["universe"]["codes"]),
        history_days=max(540, horizon * 2),
        universe_codes=prepared_request["universe"]["codes"],
    )
    policy = trusted_portfolio_policy()
    if cancel_check():
        raise ResearchCancelled("用户取消")

    composites: list[dict[str, Any]] = []
    signal_groups: list[dict[str, Any]] = expanded["signal_combinations"]
    exit_groups: list[dict[str, Any]] = expanded["exit_combinations"]
    for index, signal_params in enumerate(signal_groups):
        group_number = index + 1
        base_pct = 5 + int(56 * index / max(1, len(signal_groups)))
        next_pct = 5 + int(56 * (index + 1) / max(1, len(signal_groups)))
        middle_pct = base_pct + max(1, (next_pct - base_pct) // 2)

        def window_progress(
            window_label: str,
            start_pct: int,
            end_pct: int,
            current_group: int = group_number,
        ) -> Callable[[str, int], None]:
            def callback(message: str, local_pct: int) -> None:
                bounded = max(0, min(100, int(local_pct)))
                overall = start_pct + int((end_pct - start_pct) * bounded / 100)
                progress(
                    "GRID",
                    overall,
                    f"参数组 {current_group}/{len(signal_groups)} · {window_label} · {message}",
                )

            return callback

        progress(
            "GRID",
            base_pct,
            f"参数组 {index + 1}/{len(signal_groups)}：IS 与 OOS 净成本回放",
        )
        exit_combos = [{"strategy": "A", **item} for item in exit_groups]
        is_frame = run_grid(
            start=windows["is"][0],
            end=windows["is"][1],
            strategy="A",
            step=prepared_request["sample_step"],
            max_codes=len(snapshot.universe),
            horizon=horizon,
            workers=None,
            progress_cb=window_progress("IS", base_pct, middle_pct),
            cancel_check=cancel_check,
            signal_kwargs=signal_params,
            portfolio_policy=policy,
            research_snapshot=snapshot,
            combos_override=exit_combos,
        )
        oos_frame = run_grid(
            start=windows["oos"][0],
            end=windows["oos"][1],
            strategy="A",
            step=prepared_request["sample_step"],
            max_codes=len(snapshot.universe),
            horizon=horizon,
            workers=None,
            progress_cb=window_progress("OOS", middle_pct, next_pct),
            cancel_check=cancel_check,
            signal_kwargs=signal_params,
            portfolio_policy=policy,
            research_snapshot=snapshot,
            combos_override=exit_combos,
        )
        is_map = _rows_by_exit(is_frame)
        oos_map = _rows_by_exit(oos_frame)
        for exit_params in exit_groups:
            key = _exit_key(exit_params)
            composite = {"signal": signal_params, "exit": exit_params}
            composite_id = hashlib.sha256(
                json.dumps(composite, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16]
            composites.append(
                {
                    "param_id": composite_id,
                    **composite,
                    "is": _metric_subset(is_map.get(key)),
                    "oos": _metric_subset(oos_map.get(key)),
                }
            )
        progress("GRID", next_pct, f"参数组 {index + 1}/{len(signal_groups)} 完成")

    leaderboard = sorted(composites, key=_is_rank, reverse=True)
    path_analysis = _path_analysis(leaderboard)
    independent_leaderboard = _independent_leaderboard(leaderboard)
    best = next((row for row in leaderboard if int(row["is"].get("net_n_trades") or 0) > 0), None)
    if best is None:
        return {
            "status": "done",
            "verdict": "EVIDENCE_INSUFFICIENT",
            "verdict_label": "证据不足：没有组合达到最小成交样本",
            "request": prepared_request,
            "snapshot": snapshot.identity(),
            "leaderboard": leaderboard[:100],
            "independent_leaderboard": independent_leaderboard[:100],
            "path_analysis": path_analysis,
            "selected": None,
            "wf": None,
            "baselines": None,
            "cost_stress": None,
            "candidate_eligible": False,
            "can_claim_edge": False,
            "warnings": _warnings(prepared_request),
        }

    progress("WF", 64, "对样本内选出的第一名执行滚动窗口复验")
    wf: dict[str, Any] | None = None
    if wf_windows:
        wf_frame = wf_recheck(
            [{"strategy": "A", **best["exit"]}],
            step=prepared_request["sample_step"],
            max_codes=len(snapshot.universe),
            windows=wf_windows,
            cancel_check=cancel_check,
            signal_kwargs=best["signal"],
            portfolio_policy=policy,
            research_snapshot=snapshot,
            horizon=horizon,
        )
        if not wf_frame.empty:
            wf = _clean(wf_frame.iloc[0].to_dict())

    progress("BASELINES", 78, "生成固定种子随机与 MA20/60 基线")
    market = snapshot.load_daily(start=snapshot.data_start, end=windows["oos"][1])
    n_trades = max(20, int(best["oos"].get("net_n_trades") or 40))
    hold_days = int(best["exit"]["max_hold_days"])
    baselines = {
        "random": normalize_portfolio_metrics(_clean(
            random_baseline_trades(
                market,
                n_trades=n_trades,
                hold_days=hold_days,
                entry_start=windows["oos"][0],
                entry_end=windows["oos"][1],
                codes=list(snapshot.universe),
                portfolio_policy=policy,
            )
        )),
        "ma20_60": normalize_portfolio_metrics(_clean(
            ma_cross_baseline(
                market,
                hold_days=hold_days,
                max_trades=n_trades,
                entry_start=windows["oos"][0],
                entry_end=windows["oos"][1],
                codes=list(snapshot.universe),
                portfolio_policy=policy,
            )
        )),
    }

    progress("COST", 88, "对入选组合执行 2 倍成本压力")
    stress_policy = replace(policy, cost_multiplier_bps=20_000)
    stress = run_single_backtest(
        strategy="A",
        exit_params=best["exit"],
        signal_kwargs=best["signal"],
        start=windows["oos"][0],
        end=windows["oos"][1],
        step=prepared_request["sample_step"],
        max_codes=len(snapshot.universe),
        cancel_check=cancel_check,
        portfolio_policy=stress_policy,
        research_snapshot=snapshot,
        horizon=horizon,
    )
    if stress.get("error"):
        raise RuntimeError(str(stress["error"]))
    cost_stress = {
        "multiplier": "2x",
        "policy_fingerprint": stress_policy.fingerprint(),
        "metrics": normalize_portfolio_metrics(_clean(stress.get("metrics") or {})),
    }

    progress("REPORT", 96, "生成探索性研究报告")
    verdict, verdict_label, reasons = _verdict(best, wf, baselines, cost_stress)
    result = {
        "status": "done",
        "verdict": verdict,
        "verdict_label": verdict_label,
        "verdict_reasons": reasons,
        "request": prepared_request,
        "snapshot": snapshot.identity(),
        "portfolio_policy": {
            "version": policy.version,
            "fingerprint": policy.fingerprint(),
        },
        "leaderboard": [_clean(row) for row in leaderboard[:100]],
        "independent_leaderboard": [
            _clean(row) for row in independent_leaderboard[:100]
        ],
        "evaluated_combinations": len(leaderboard),
        "path_analysis": path_analysis,
        "selected": _clean(best),
        "wf": wf,
        "baselines": baselines,
        "cost_stress": cost_stress,
        "candidate_eligible": False,
        "can_claim_edge": False,
        "warnings": _warnings(prepared_request),
    }
    result["report_markdown"] = _report_markdown(result)
    progress("REPORT", 100, "专业回测完成")
    return result


def _resolve_windows(raw: Any, dates: list[str]) -> dict[str, Any]:
    from research_windows import recommend_research_plan

    if not dates:
        raise ProfessionalGridError("NO_MARKET_DATA", "本地数据库没有日线")
    if isinstance(raw, dict) and str(raw.get("mode") or "auto") == "manual":
        required = ("is_start", "is_end", "oos_start", "oos_end")
        values = {key: str(raw.get(key) or "") for key in required}
        if any(len(value) != 8 or not value.isdigit() for value in values.values()):
            raise ProfessionalGridError("INVALID_WINDOWS", "手动窗口必须为 YYYYMMDD")
        if not (values["is_start"] <= values["is_end"] < values["oos_start"] <= values["oos_end"]):
            raise ProfessionalGridError("INVALID_WINDOWS", "IS 与 OOS 必须按时间先后且不重叠")
        if values["is_start"] < dates[0] or values["oos_end"] > dates[-1]:
            raise ProfessionalGridError(
                "WINDOW_OUTSIDE_DATA", "手动窗口超出本地日线范围",
                {"earliest": dates[0], "latest": dates[-1]},
            )
        return {
            "mode": "manual",
            "is": [values["is_start"], values["is_end"]],
            "oos": [values["oos_start"], values["oos_end"]],
            "wf": [],
            "n_dates": len(dates),
        }
    plan = recommend_research_plan(dates)
    if plan.mode == "insufficient":
        raise ProfessionalGridError(
            "INSUFFICIENT_HISTORY", "历史交易日不足以拆分 IS/OOS", {"n_dates": plan.n_dates}
        )
    return {
        "mode": plan.mode,
        "label": plan.label,
        "is": [plan.is_start, plan.is_end],
        "oos": [plan.oos_start, plan.oos_end],
        "wf": [
            {"train_start": a, "train_end": b, "test_start": c, "test_end": d}
            for a, b, c, d in plan.wf_windows
        ],
        "n_dates": plan.n_dates,
        "earliest": plan.earliest,
        "latest": plan.latest,
        "data_ready_for_edge_validation": plan.data_ready_for_edge_validation,
        "notes": plan.notes,
    }


def _distinct_dates(db_path: str | Path) -> list[str]:
    path = Path(db_path).resolve()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute("SELECT DISTINCT trade_date FROM daily ORDER BY trade_date").fetchall()
    return [str(row[0]) for row in rows]


def _rows_by_exit(frame: pd.DataFrame) -> dict[tuple[Any, ...], dict[str, Any]]:
    if frame is None or frame.empty:
        return {}
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for _, row in frame.iterrows():
        payload = row.to_dict()
        result[_exit_key(payload)] = payload
    return result


def _exit_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        round(float(payload["vol_ratio_min"]), 10),
        int(payload["strong_reset"]),
        int(payload["exit_window"]),
        int(payload["max_hold_days"]),
        round(float(payload["stop_pct"]), 10),
        round(float(payload["target_pct"]), 10),
    )


_METRICS = (
    "n_trades", "win_rate", "avg_ret", "profit_factor", "max_drawdown",
    "net_n_trades", "net_win_rate", "net_avg_return", "net_profit_factor",
    "net_max_drawdown", "portfolio_status", "portfolio_total_return",
    "portfolio_max_drawdown", "portfolio_final_equity_fen", "portfolio_rejected_count",
    "portfolio_equity_sha256",
)


def _metric_subset(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"net_n_trades": 0, "evidence_complete": False}
    result = normalize_portfolio_metrics(
        {key: _clean(row.get(key)) for key in _METRICS if key in row}
    )
    result["evidence_complete"] = bool(
        int(result.get("net_n_trades") or 0) > 0
        and result.get("portfolio_status") == "PASS"
    )
    return result


def _is_rank(row: dict[str, Any]) -> tuple[float, float, int]:
    metrics = row["is"]
    return (
        _finite(metrics.get("net_profit_factor"), -1.0),
        _finite(portfolio_total_return(metrics), -1.0),
        int(metrics.get("net_n_trades") or 0),
    )


def _finite(value: Any, fallback: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def _clean(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _path_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count exact portfolio paths; never infer equality from rounded metrics."""
    nominal = len(rows)
    if not rows:
        return {
            "method": "combined_portfolio_equity_sha256",
            "evidence_complete": False,
            "nominal_combinations": 0,
            "independent_is_paths": None,
            "independent_oos_paths": None,
            "independent_joint_paths": None,
            "duplicate_group_count": 0,
        }
    identities: list[tuple[str, str]] = []
    for row in rows:
        is_hash = str((row.get("is") or {}).get("portfolio_equity_sha256") or "")
        oos_hash = str((row.get("oos") or {}).get("portfolio_equity_sha256") or "")
        if not is_hash or not oos_hash:
            return {
                "method": "combined_portfolio_equity_sha256",
                "evidence_complete": False,
                "nominal_combinations": nominal,
                "independent_is_paths": None,
                "independent_oos_paths": None,
                "independent_joint_paths": None,
                "duplicate_group_count": 0,
            }
        identities.append((is_hash, oos_hash))
    group_sizes: dict[tuple[str, str], int] = {}
    for identity in identities:
        group_sizes[identity] = group_sizes.get(identity, 0) + 1
    return {
        "method": "combined_portfolio_equity_sha256",
        "evidence_complete": True,
        "nominal_combinations": nominal,
        "independent_is_paths": len({identity[0] for identity in identities}),
        "independent_oos_paths": len({identity[1] for identity in identities}),
        "independent_joint_paths": len(group_sizes),
        "duplicate_group_count": sum(size > 1 for size in group_sizes.values()),
    }


def _independent_leaderboard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only rows proven to share the exact IS and OOS equity paths."""
    result: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        is_hash = str((row.get("is") or {}).get("portfolio_equity_sha256") or "")
        oos_hash = str((row.get("oos") or {}).get("portfolio_equity_sha256") or "")
        if not is_hash or not oos_hash:
            result.append({**row, "equivalent_parameter_count": 1})
            continue
        identity = (is_hash, oos_hash)
        existing = by_identity.get(identity)
        if existing is None:
            representative = {**row, "equivalent_parameter_count": 1}
            by_identity[identity] = representative
            result.append(representative)
        else:
            existing["equivalent_parameter_count"] = (
                int(existing["equivalent_parameter_count"]) + 1
            )
    return result


def _verdict(
    best: dict[str, Any],
    wf: dict[str, Any] | None,
    baselines: dict[str, Any],
    stress: dict[str, Any],
) -> tuple[str, str, list[str]]:
    oos = best["oos"]
    reasons: list[str] = []
    oos_n = int(oos.get("net_n_trades") or 0)
    if oos_n < 30:
        reasons.append(f"OOS 实际成交仅 {oos_n} 笔，低于 30 笔最低解释门槛")
    if not wf or not bool(wf.get("evidence_complete")):
        reasons.append("滚动窗口证据不完整")
    elif not bool(wf.get("wf_pass")):
        reasons.append("滚动窗口稳定性未通过")
    stress_return = portfolio_total_return(stress.get("metrics") or {})
    if stress_return is None or float(stress_return) <= 0:
        reasons.append("2 倍成本下组合收益未保持为正")
    candidate_return = portfolio_total_return(oos)
    baseline_returns = [
        value
        for item in baselines.values()
        if (value := portfolio_total_return(item)) is not None
    ]
    if candidate_return is None or not baseline_returns or not all(
        float(candidate_return) > float(value) for value in baseline_returns
    ):
        reasons.append("OOS 组合收益未同时超过随机与 MA20/60 基线")
    if reasons:
        return "EXPLORATORY_WEAK", "探索结果未达到预登记候选条件", reasons[:4]
    return (
        "EXPLORATORY_PROMISING",
        "探索结果值得另行预登记复验",
        ["本次结果仍使用已观察参数空间，不能直接晋级或宣称存在可交易 edge"],
    )


def _warnings(request: dict[str, Any]) -> list[str]:
    return [
        request["universe"]["classification_note"],
        "所有信号在收盘确认，最早下一可交易日开盘成交。",
        "本页是探索性参数研究；选择第一名使用 IS，OOS/WF 只做验证。",
        "筹码条件扩展口已建立，但在经济机制预登记完成前保持关闭。",
        "AI 评测不参与本回测、扫描池或候选晋级。",
    ]


def _report_markdown(result: dict[str, Any]) -> str:
    selected = result.get("selected") or {}
    universe = result["request"]["universe"]
    groups = universe.get("groups") or []
    group_text = "、".join(str(value) for value in groups) if groups else "全市场"
    return "\n".join(
        [
            "# AB-Screener 专业回测报告",
            "",
            f"- 结论：{result['verdict_label']}",
            f"- 参数空间：{result['request']['parameter_space']['count']} 组，"
            f"SHA-256 `{result['request']['parameter_space']['sha256']}`",
            (
                "- 独立收益路径："
                f"{result['path_analysis']['independent_joint_paths']} / "
                f"{result['path_analysis']['nominal_combinations']}"
                if result.get("path_analysis", {}).get("evidence_complete")
                else "- 独立收益路径：证据不完整（缺组合权益哈希）"
            ),
            f"- 冻结股票池：{result['request']['universe']['count']} 只，"
            f"SHA-256 `{result['request']['universe']['sha256']}`",
            f"- 分类标准：{universe.get('classification_title', '细分行业')}，分组：{group_text}",
            f"- PIT 数据：`{result['snapshot']['dataset_fingerprint']}`",
            f"- IS：{result['request']['windows']['is'][0]} ~ {result['request']['windows']['is'][1]}",
            f"- OOS：{result['request']['windows']['oos'][0]} ~ {result['request']['windows']['oos'][1]}",
            f"- 入选参数：`{json.dumps(selected.get('signal'), ensure_ascii=False, sort_keys=True)}` / "
            f"`{json.dumps(selected.get('exit'), ensure_ascii=False, sort_keys=True)}`",
            "- 晋级边界：candidate_eligible=false；本报告不自动改变生产策略或每日选股。",
            "",
            "## 主要阻断/说明",
            *[f"- {item}" for item in result.get("verdict_reasons") or result.get("warnings", [])],
        ]
    )
