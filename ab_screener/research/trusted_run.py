"""Orchestration for the persistent, trusted Lab validation workflow."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from ab_screener.domain.costs import (
    COMMISSION_MIN,
    COMMISSION_RATE,
    NOTIONAL,
    OTHER_FEE_RATE,
    SLIPPAGE,
    STAMP_TAX_SELL,
)
from ab_screener.domain.entry_registry import active_definition_id as _active_entry_definition_id
from ab_screener.domain.entry_registry import report_entry_fingerprint
from ab_screener.domain.execution.models import EXECUTION_MODEL_VERSION, FEE_VERSION
from ab_screener.research.baselines import ma_cross_baseline, random_baseline_trades
from ab_screener.research.formal_evidence import (
    FORMAL_EVIDENCE_VERSION,
    formal_identity_valid,
    parameter_neighborhood_evidence,
    statistical_formal_evidence,
)
from ab_screener.research.pit_reader import (
    ResearchPitSnapshot,
    build_research_pit_snapshot,
    latest_research_cutoff,
)
from ab_screener.research.portfolio_accounting import (
    PortfolioPolicy,
    load_portfolio_policy,
)
from ab_screener.research.regime_filter import (
    PRODUCTION_REGIME_ENTRY_POLICY,
    ResearchRegimeFilter,
    build_research_regime_filter,
)
from ab_screener.research.reporting import freeze_is_winner, render_trusted_report
from ab_screener.research.validation import evaluate_personal_anti_overfit, evaluate_trusted_gate

PhaseCallback = Callable[[str, int, str, dict[str, Any]], None]
CancelCheck = Callable[[], bool]

COST_ASSUMPTIONS = {
    "notional": NOTIONAL,
    "lot_size": 100,
    "commission_rate_each_side": COMMISSION_RATE,
    "minimum_commission_each_side": COMMISSION_MIN,
    "sell_stamp_tax": STAMP_TAX_SELL,
    "other_fee_each_side": OTHER_FEE_RATE,
    "slippage_each_side": SLIPPAGE,
    "description": "固定研究假设，不代表实际券商费率",
}
COST_VERSION = hashlib.sha256(json.dumps(COST_ASSUMPTIONS, sort_keys=True).encode("utf-8")).hexdigest()[:16]
DEFAULT_PORTFOLIO_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "research" / "portfolio_v2.yaml"
)


def trusted_portfolio_policy() -> PortfolioPolicy:
    """Load the versioned account policy used by every authoritative run."""
    return load_portfolio_policy(DEFAULT_PORTFOLIO_POLICY_PATH)


def trusted_portfolio_identity() -> dict[str, str]:
    policy = trusted_portfolio_policy()
    return {
        "version": policy.version,
        "config_hash": policy.fingerprint(),
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "fee_version": FEE_VERSION,
    }


def _clean(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _extract_formal_series(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """Keep formal daily returns in the checkpoint, never in public rankings."""
    public: list[dict[str, Any]] = []
    series: dict[str, dict[str, float]] = {}
    for raw in records:
        row = dict(raw)
        formal = row.pop("_formal_daily_returns", None)
        param_id = str(row.get("param_id") or "")
        if param_id and isinstance(formal, dict):
            series[param_id] = {str(date): float(value) for date, value in formal.items()}
        public.append(row)
    return public, series


def _net_total(row: dict[str, Any]) -> float | None:
    value = row.get("net_total_return", row.get("net_avg_return"))
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def dataset_fingerprint(db_path: str | Path, *, start: str, end: str, codes: list[str]) -> str:
    """Hash the exact OHLCV subset used by a Lab run."""
    digest = hashlib.sha256()
    if not codes:
        return digest.hexdigest()[:16]
    placeholders = ",".join("?" for _ in codes)
    sql = (
        "SELECT ts_code,trade_date,open,high,low,close,pre_close,vol "
        f"FROM daily WHERE trade_date>=? AND trade_date<=? AND ts_code IN ({placeholders}) "
        "ORDER BY ts_code,trade_date"
    )
    count = 0
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        cursor = conn.execute(sql, (start, end, *codes))
        for row in cursor:
            digest.update(
                ("|".join("" if item is None else str(item) for item in row) + "\n").encode("utf-8")
            )
            count += 1
    digest.update(f"rows={count}".encode("ascii"))
    return digest.hexdigest()[:16]


def input_fingerprint(
    request: dict[str, Any],
    windows: dict[str, Any],
    *,
    dataset_version: str,
    code_version: str,
    cost_version: str = COST_VERSION,
) -> str:
    payload = {
        "request": request,
        "windows": windows,
        "dataset_version": dataset_version,
        "code_version": code_version,
        "cost_version": cost_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _wf_tuples(windows: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    result: list[tuple[str, str, str, str]] = []
    for row in windows.get("wf_windows") or []:
        if isinstance(row, dict):
            values = (
                row.get("train_start"),
                row.get("train_end"),
                row.get("test_start"),
                row.get("test_end"),
            )
        else:
            values = tuple(row)
        if len(values) == 4 and all(values):
            result.append((str(values[0]), str(values[1]), str(values[2]), str(values[3])))
    return result


def prepare_trusted_pit_snapshot(
    db_path: str | Path,
    *,
    windows: dict[str, Any],
    max_codes: int,
    decision_at: str | None = None,
) -> ResearchPitSnapshot:
    """Build the single PIT snapshot shared by IS/OOS/WF/baselines."""
    starts = [str(windows["is_start"]), str(windows["oos_start"])]
    ends = [str(windows["is_end"]), str(windows["oos_end"])]
    for train_start, train_end, test_start, test_end in _wf_tuples(windows):
        starts.extend((train_start, test_start))
        ends.extend((train_end, test_end))
    resolved_path = str(Path(db_path).resolve())
    cutoff = decision_at or latest_research_cutoff(resolved_path)
    return _prepare_trusted_pit_snapshot_cached(
        resolved_path,
        min(starts),
        max(ends),
        max_codes,
        cutoff,
    )


def prepare_trusted_regime_filter(
    snapshot: ResearchPitSnapshot,
    *,
    windows: dict[str, Any],
    entry_policy: str = PRODUCTION_REGIME_ENTRY_POLICY,
) -> ResearchRegimeFilter:
    """Freeze one causal, explicitly identified regime filter for a run."""
    starts = [str(windows["is_start"]), str(windows["oos_start"])]
    ends = [str(windows["is_end"]), str(windows["oos_end"])]
    for train_start, train_end, test_start, test_end in _wf_tuples(windows):
        starts.extend((train_start, test_start))
        ends.extend((train_end, test_end))
    return build_research_regime_filter(
        snapshot,
        start=min(starts),
        end=max(ends),
        entry_policy=entry_policy,
    )


@lru_cache(maxsize=1)
def _prepare_trusted_pit_snapshot_cached(
    db_path: str,
    study_start: str,
    study_end: str,
    max_codes: int,
    decision_at: str,
) -> ResearchPitSnapshot:
    """Reuse one immutable snapshot between preregistration and its worker."""
    return build_research_pit_snapshot(
        db_path,
        study_start=study_start,
        study_end=study_end,
        max_codes=max_codes,
        decision_at=decision_at,
        benchmark_code="000300.SH",
    )


def _cost_stress_evidence(
    *,
    primary_is: dict[str, Any] | None,
    primary_oos: dict[str, Any],
    primary_baseline: str,
    windows: dict[str, Any],
    step: int,
    max_codes: int,
    universe: list[str],
    db_path: str | Path,
    portfolio_policy: PortfolioPolicy,
    research_snapshot: ResearchPitSnapshot | None,
    allowed_signal_dates: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Replay candidate and preregistered baseline under exactly 2× account costs."""
    if not primary_is:
        return {"status": "INSUFFICIENT", "reason": "无冻结 IS 第一名"}
    if primary_baseline not in {"ma20_60", "random"}:
        return {"status": "INSUFFICIENT", "reason": "未预登记受支持的主基线"}
    from ab_screener.research.backtest_engine import run_single_backtest

    stress_policy = replace(portfolio_policy, cost_multiplier_bps=20_000)
    candidate = run_single_backtest(
        strategy=str(primary_is.get("strategy") or "A"),
        exit_params={
            key: primary_is[key]
            for key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
            if key in primary_is
        }
        or None,
        start=str(windows["oos_start"]),
        end=str(windows["oos_end"]),
        step=step,
        max_codes=max_codes,
        portfolio_policy=stress_policy,
        research_snapshot=research_snapshot,
        allowed_signal_dates=allowed_signal_dates,
    )
    candidate_portfolio = candidate.get("portfolio") or {}
    hold_days = int(primary_is.get("exit_window") or 10)
    requested_trades = int(primary_oos.get("oos_net_n_trades") or 40)
    load_start = (pd.to_datetime(windows["oos_start"]) - pd.Timedelta(days=365)).strftime("%Y%m%d")
    if research_snapshot is not None:
        daily = research_snapshot.load_daily(
            ts_codes=universe,
            start=load_start,
            end=str(windows["oos_end"]),
        )
    else:
        from local_store import LocalStore

        daily = LocalStore(db_path).load_daily(
            ts_codes=universe,
            start=load_start,
            end=str(windows["oos_end"]),
        )
    baseline_kwargs = {
        "daily": daily,
        "hold_days": hold_days,
        "entry_start": str(windows["oos_start"]),
        "entry_end": str(windows["oos_end"]),
        "codes": universe,
        "portfolio_policy": stress_policy,
        "allowed_signal_dates": allowed_signal_dates,
    }
    if primary_baseline == "ma20_60":
        baseline = ma_cross_baseline(max_trades=requested_trades, **baseline_kwargs)
    else:
        baseline = random_baseline_trades(n_trades=requested_trades, **baseline_kwargs)
    candidate_total = (
        float(candidate_portfolio["portfolio_total_return"])
        if candidate_portfolio.get("portfolio_status") == "PASS"
        and candidate_portfolio.get("portfolio_total_return") is not None
        else None
    )
    baseline_total = _net_total(baseline) if baseline.get("portfolio_status") == "PASS" else None
    status = "OK" if candidate_total is not None and baseline_total is not None else "INCOMPLETE"
    return {
        "status": status,
        "cost_multiplier_bps": stress_policy.cost_multiplier_bps,
        "portfolio_model_version": stress_policy.version,
        "portfolio_config_hash": stress_policy.fingerprint(),
        "candidate_portfolio_status": candidate_portfolio.get("portfolio_status"),
        "baseline": primary_baseline,
        "baseline_portfolio_status": baseline.get("portfolio_status"),
        "candidate_net_total_2x": candidate_total,
        "baseline_net_total_2x": baseline_total,
        "candidate_equity_sha256": candidate_portfolio.get("portfolio_equity_sha256"),
        "baseline_equity_sha256": baseline.get("portfolio_equity_sha256"),
    }


def execute_trusted_research(
    *,
    research_run_id: str,
    request: dict[str, Any],
    windows: dict[str, Any],
    db_path: str | Path,
    code_version: str,
    dataset_version: str,
    phase_cb: PhaseCallback,
    checkpoint: dict[str, Any] | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    """Execute/resume all trusted stages and return the API/report payload."""
    from optimizer import research_universe
    from walkforward import run_is_oos, wf_recheck

    state = dict(checkpoint or {})
    portfolio_policy = trusted_portfolio_policy()
    portfolio_identity = trusted_portfolio_identity()
    requested_portfolio = request.get("portfolio_model")
    if requested_portfolio is not None and requested_portfolio != portfolio_identity:
        raise ValueError("请求绑定的组合模型与当前权威配置不一致")
    strategy = str(request.get("strategy") or "A")
    run_mode = str(request.get("mode") or "grid")
    max_codes = max(20, min(int(request.get("max_codes") or 200), 4500))
    step = max(1, min(int(request.get("step") or 10), 60))
    requested_pit = request.get("pit_snapshot")
    requested_regime_filter = request.get("market_regime_filter")
    research_snapshot: ResearchPitSnapshot | None = None
    pit_identity: dict[str, Any] | None = None
    regime_filter: ResearchRegimeFilter | None = None
    regime_identity: dict[str, Any] | None = None
    allowed_signal_dates: frozenset[str] | None = None
    if requested_pit is not None:
        if not isinstance(requested_pit, dict) or not requested_pit.get("decision_at"):
            raise ValueError("请求缺少有效的 PIT knowledge cutoff")
        research_snapshot = prepare_trusted_pit_snapshot(
            db_path,
            windows=windows,
            max_codes=max_codes,
            decision_at=str(requested_pit["decision_at"]),
        )
        pit_identity = research_snapshot.identity()
        if requested_pit != pit_identity:
            raise ValueError("请求绑定的 PIT 快照与当前可复算结果不一致")
        if dataset_version != research_snapshot.dataset_fingerprint:
            raise ValueError("预登记数据版本与 PIT 快照指纹不一致")
        if not isinstance(requested_regime_filter, dict):
            raise ValueError("权威 PIT 研究必须预登记市场状态门禁")
        regime_entry_policy = str(
            requested_regime_filter.get("entry_policy") or PRODUCTION_REGIME_ENTRY_POLICY
        )
        regime_filter = prepare_trusted_regime_filter(
            research_snapshot,
            windows=windows,
            entry_policy=regime_entry_policy,
        )
        regime_identity = regime_filter.identity()
        if requested_regime_filter != regime_identity:
            raise ValueError("请求绑定的市场状态门禁与当前可复算结果不一致")
        allowed_signal_dates = regime_filter.allowed_signal_dates
        universe = list(research_snapshot.universe)
    else:
        if requested_regime_filter is not None:
            raise ValueError("市场状态门禁必须绑定 PIT 快照")
        universe = research_universe(max_codes, include_delisted=True)

    def ensure_not_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            from optimizer import ResearchCancelled

            raise ResearchCancelled("用户取消")

    needs_formal_grid = run_mode == "grid" and not state.get("formal_is_returns")
    if not state.get("is_all") or "oos_all" not in state or needs_formal_grid:
        ensure_not_cancelled()
        phase_cb("IS", 5, "样本内净成本搜索", state)
        result = run_is_oos(
            strategy=strategy,
            step=step,
            max_codes=max_codes,
            top_n=3,
            progress_cb=lambda message, pct: phase_cb(
                "OOS" if str(message).startswith("OOS") else "IS",
                min(70, max(5, int(pct * 0.70))),
                str(message),
                state,
            ),
            is_start=windows["is_start"],
            is_end=windows["is_end"],
            oos_start=windows["oos_start"],
            oos_end=windows["oos_end"],
            grid=request.get("grid"),
            single=(
                {key: request[key] for key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
                if run_mode == "single"
                else None
            ),
            cancel_check=cancel_check,
            portfolio_policy=portfolio_policy,
            research_snapshot=research_snapshot,
            capture_formal_series=run_mode == "grid",
            allowed_signal_dates=allowed_signal_dates,
        )
        raw_is = result["is"].to_dict("records") if not result["is"].empty else []
        raw_oos = result["oos"].to_dict("records") if not result["oos"].empty else []
        public_is, formal_is = _extract_formal_series(raw_is)
        public_oos, _formal_oos = _extract_formal_series(raw_oos)
        state["is_all"] = _clean(public_is)
        state["oos_all"] = _clean(public_oos)
        state["formal_is_returns"] = _clean(formal_is)
        state["neighborhood_param_ids"] = _clean(result.get("neighborhood_param_ids") or [])
        state["run_message"] = result.get("msg")
        phase_cb("OOS", 70, "主候选样本外验证完成", state)

    frozen = freeze_is_winner(state.get("is_all") or [], state.get("oos_all") or [])
    primary_is = frozen["primary_is"]
    primary_oos = frozen["primary_oos"] or {}

    if "wf_windows" not in state:
        ensure_not_cancelled()
        phase_cb("WF", 73, "三窗口 Walk-forward 净成本复核", state)
        wf_rows: list[dict[str, Any]] = []
        wf_windows = _wf_tuples(windows)
        if primary_is and wf_windows:
            combo = {
                key: primary_is.get(key)
                for key in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
            }
            wf_df = wf_recheck(
                [combo],
                step=step,
                max_codes=max_codes,
                windows=wf_windows,
                progress_cb=lambda message, pct: phase_cb(
                    "WF",
                    73 + int(13 * pct / 100),
                    str(message),
                    state,
                ),
                cancel_check=cancel_check,
                portfolio_policy=portfolio_policy,
                research_snapshot=research_snapshot,
                allowed_signal_dates=allowed_signal_dates,
            )
            if not wf_df.empty:
                wf_rows = _clean(wf_df.iloc[0].get("wf_detail") or [])
        state["wf_windows"] = wf_rows
        phase_cb("WF", 86, "Walk-forward 完成", state)

    if "baselines" not in state:
        ensure_not_cancelled()
        phase_cb("BASELINES", 88, "运行固定种子随机与 MA20/60 基线", state)
        baseline_rows: dict[str, Any] = {}
        if primary_is:
            hold_days = int(primary_is.get("exit_window") or 10)
            requested_trades = int(primary_oos.get("oos_net_n_trades") or 40)
            load_start = (pd.to_datetime(windows["oos_start"]) - pd.Timedelta(days=365)).strftime("%Y%m%d")
            if research_snapshot is not None:
                daily = research_snapshot.load_daily(
                    ts_codes=universe,
                    start=load_start,
                    end=windows["oos_end"],
                )
            else:
                from local_store import LocalStore

                daily = LocalStore(db_path).load_daily(
                    ts_codes=universe,
                    start=load_start,
                    end=windows["oos_end"],
                )
            baseline_rows = {
                "random": random_baseline_trades(
                    daily,
                    n_trades=requested_trades,
                    hold_days=hold_days,
                    entry_start=windows["oos_start"],
                    entry_end=windows["oos_end"],
                    codes=universe,
                    portfolio_policy=portfolio_policy,
                    allowed_signal_dates=allowed_signal_dates,
                ),
                "ma20_60": ma_cross_baseline(
                    daily,
                    hold_days=hold_days,
                    max_trades=requested_trades,
                    entry_start=windows["oos_start"],
                    entry_end=windows["oos_end"],
                    codes=universe,
                    portfolio_policy=portfolio_policy,
                    allowed_signal_dates=allowed_signal_dates,
                ),
            }
        ensure_not_cancelled()
        state["baselines"] = _clean(baseline_rows)
        phase_cb("BASELINES", 93, "双基线完成", state)

    ensure_not_cancelled()
    phase_cb("GATE", 95, "执行可信研究门禁", state)
    anti_overfit = evaluate_personal_anti_overfit(
        is_candidates=state.get("is_all") or [],
        oos_candidates=state.get("oos_all") or [],
        wf_windows=state.get("wf_windows") or [],
    )
    gate = evaluate_trusted_gate(
        research_mode=str(windows.get("mode") or "manual"),
        automatic_window=bool(windows.get("automatic_window")),
        run_mode=run_mode,
        oos=primary_oos,
        wf_windows=state.get("wf_windows") or [],
        baselines=state.get("baselines") or {},
        anti_overfit=anti_overfit,
        portfolio_model=portfolio_identity if requested_portfolio is not None else None,
        pit_snapshot=pit_identity if requested_pit is not None else None,
    )
    state["gate"] = gate
    state["anti_overfit"] = anti_overfit
    phase_cb("REPORT", 97, "生成一页可信报告", state)
    nominal_n_trials = max(1, len(state.get("is_all") or []))
    report = {
        "research_run_id": research_run_id,
        **gate,
        "versions": {
            "dataset": dataset_version,
            "code": code_version,
            "cost": COST_VERSION,
            "entry": report_entry_fingerprint(_active_entry_definition_id()),
            "portfolio": portfolio_identity["config_hash"],
            "pit_reader": pit_identity.get("version") if pit_identity else None,
            "market_regime_filter": regime_identity.get("version") if regime_identity else None,
        },
        "portfolio_model": portfolio_identity,
        "point_in_time": pit_identity,
        "market_regime_filter": regime_identity,
        "sample": {"universe_size": len(universe), "windows": windows, "step": step},
        "cost_assumptions": COST_ASSUMPTIONS,
        "primary_is": primary_is,
        "primary_oos": primary_oos or None,
        "wf_windows": state.get("wf_windows") or [],
        "baselines": state.get("baselines") or {},
        "sensitivity": frozen["sensitivity"],
        "anti_overfit": anti_overfit,
        "multiple_comparison": {
            "grid_trials": nominal_n_trials,
            "correction": "pending_formal_evidence",
            "note": (
                f"网格共 {nominal_n_trials} 组名义参数；最终有效试验数以精确收益路径去重后"
                "的正式证据块为准。"
            ),
        },
    }
    formal_statistics = statistical_formal_evidence(state.get("formal_is_returns") or {})
    return_matrix = formal_statistics.get("return_matrix") or {}
    effective_n_trials = int(return_matrix.get("effective_parameters") or 0)
    trial_sharpe_std = return_matrix.get("trial_sharpe_std")
    # P3.2：v2 正式统计（DSR/MinTRL）——primary OOS 组合每日盯市净收益；
    # 样本不足/异常 → INSUFFICIENT（不伪造）
    try:
        from ab_screener.research.backtest_engine import run_single_backtest
        from ab_screener.research.validation import v2_statistics_block

        if return_matrix.get("status") != "OK" or effective_n_trials < 4:
            report["v2_statistics"] = {
                "status": "INSUFFICIENT",
                "reason": str(return_matrix.get("reason") or "独立收益路径不足，不能计算正式统计"),
            }
        elif primary_is:
            bt = run_single_backtest(
                strategy=str(primary_is.get("strategy") or "A"),
                exit_params={
                    k: primary_is[k]
                    for k in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
                    if k in primary_is
                }
                or None,
                start=windows["oos_start"],
                end=windows["oos_end"],
                step=step,
                max_codes=max_codes,
                portfolio_policy=portfolio_policy,
                research_snapshot=research_snapshot,
                allowed_signal_dates=allowed_signal_dates,
            )
            portfolio = bt.get("portfolio") or {}
            if portfolio.get("portfolio_status") != "PASS":
                report["v2_statistics"] = {
                    "status": "INSUFFICIENT",
                    "reason": "组合回放未完整平仓，不能计算正式统计",
                }
            else:
                oos_returns = [float(value) for value in (portfolio.get("portfolio_daily_returns") or [])]
                report["v2_statistics"] = v2_statistics_block(
                    oos_returns,
                    n_trials=effective_n_trials,
                    trial_sharpe_std=trial_sharpe_std,
                )
        else:
            report["v2_statistics"] = {
                "status": "INSUFFICIENT",
                "reason": "无 primary 组合可回放",
            }
    except Exception as exc:  # noqa: BLE001
        report["v2_statistics"] = {
            "status": "INSUFFICIENT",
            "reason": f"无法计算 v2 统计: {type(exc).__name__}: {str(exc)[:160]}",
        }
    phase_cb("FORMAL", 98, "计算 PBO、嵌套参数复验、邻域与 2× 成本压力", state)
    formal: dict[str, Any] = {
        "version": FORMAL_EVIDENCE_VERSION,
        "primary_param_id": primary_is.get("param_id") if primary_is else None,
        "primary_baseline": request.get("primary_baseline"),
        **formal_statistics,
    }
    primary_baseline = str(request.get("primary_baseline") or "")
    baseline_row = (state.get("baselines") or {}).get(primary_baseline) or {}
    formal["parameter_neighborhood"] = parameter_neighborhood_evidence(
        state.get("oos_all") or [],
        state.get("neighborhood_param_ids") or [],
        baseline_net_total=_net_total(baseline_row),
    )
    try:
        formal["cost_stress"] = _cost_stress_evidence(
            primary_is=primary_is,
            primary_oos=primary_oos,
            primary_baseline=primary_baseline,
            windows=windows,
            step=step,
            max_codes=max_codes,
            universe=universe,
            db_path=db_path,
            portfolio_policy=portfolio_policy,
            research_snapshot=research_snapshot,
            allowed_signal_dates=allowed_signal_dates,
        )
    except Exception as exc:  # noqa: BLE001 formal evidence must fail closed, not disappear
        formal["cost_stress"] = {
            "status": "INSUFFICIENT",
            "reason": f"2× 成本压力无法复算: {type(exc).__name__}",
        }
    report["formal_evidence"] = _clean(formal)
    pbo_block = formal.get("cscv_pbo") or {}
    if pbo_block.get("status") == "OK":
        report["multiple_comparison"] = {
            "grid_trials": nominal_n_trials,
            "effective_trials": effective_n_trials,
            "exact_duplicate_trials": nominal_n_trials - effective_n_trials,
            "correction": (
                "exact return-path deduplication + CSCV-PBO + Deflated Sharpe + "
                "nested parameter WF"
            ),
            "pbo": pbo_block.get("pbo"),
            "note": (
                "只合并逐日 float64 收益完全相同的路径；近似路径保持独立。参数只在训练折"
                "选择，独立测试折只评估一次，未使用 OOS 替换冻结的 IS 第一名。"
            ),
        }
    else:
        report["multiple_comparison"] = {
            "grid_trials": nominal_n_trials,
            "effective_trials": effective_n_trials,
            "correction": "incomplete",
            "note": str(pbo_block.get("reason") or "CSCV-PBO 证据不完整"),
        }
    from ab_screener.research.formal_promotion import apply_formal_promotion_gate

    identity_hashes_valid = bool(
        requested_portfolio is not None
        and requested_pit is not None
        and requested_regime_filter is not None
        and regime_identity is not None
        and code_version
        and dataset_version
        and COST_VERSION
        and formal_identity_valid(formal)
    )
    report = apply_formal_promotion_gate(
        report,
        request,
        hashes_valid=identity_hashes_valid,
    )
    report["markdown"] = render_trusted_report(report)
    state["gate"] = {
        key: report.get(key)
        for key in ("verdict", "candidate_eligible", "checks", "block_reasons", "summary")
    }
    state["report"] = report
    phase_cb(
        "CANDIDATE",
        99,
        "正式晋级门完成" if report.get("candidate_eligible") else "正式晋级门阻断候选",
        state,
    )
    return {
        "report": state["report"],
        "is_top": (state.get("is_all") or [])[:12],
        "is_all": (state.get("is_all") or [])[:40],
        "oos": state.get("oos_all") or [],
        "msg": state.get("run_message"),
        "run_mode": run_mode,
        "research_mode": windows.get("mode"),
        "can_claim_edge": bool(report.get("candidate_eligible")),
        "gross": {"note": "毛指标仅供诊断，排名和门禁只读取净成本指标"},
        "net": [
            {key: value for key, value in row.items() if "net_" in key or key in ("strategy", "param_id")}
            for row in (state.get("oos_all") or [])
        ],
        "baselines": state.get("baselines") or {},
        "promotion_checks": state["gate"],
        "params_used": request.get("grid")
        or (
            {key: request.get(key) for key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
            if run_mode == "single"
            else None
        ),
        "windows": windows,
        "trusted_report": report,
        "frozen_candidate": {"is": primary_is, "oos": primary_oos or None},
        "checkpoint": state,
    }
