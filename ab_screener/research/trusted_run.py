"""Orchestration for the persistent, trusted Lab validation workflow."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
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
from ab_screener.research.baselines import ma_cross_baseline, random_baseline_trades
from ab_screener.research.portfolio_accounting import (
    PortfolioPolicy,
    load_portfolio_policy,
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
    portfolio_identity = {
        "version": portfolio_policy.version,
        "config_hash": portfolio_policy.fingerprint(),
    }
    requested_portfolio = request.get("portfolio_model")
    if requested_portfolio is not None and requested_portfolio != portfolio_identity:
        raise ValueError("请求绑定的组合模型与当前权威配置不一致")
    strategy = str(request.get("strategy") or "A")
    run_mode = str(request.get("mode") or "grid")
    max_codes = max(20, min(int(request.get("max_codes") or 200), 4500))
    step = max(1, min(int(request.get("step") or 10), 60))
    universe = research_universe(max_codes, include_delisted=True)

    def ensure_not_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            from optimizer import ResearchCancelled

            raise ResearchCancelled("用户取消")

    if not state.get("is_all") or "oos_all" not in state:
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
        )
        state["is_all"] = _clean(result["is"].to_dict("records") if not result["is"].empty else [])
        state["oos_all"] = _clean(result["oos"].to_dict("records") if not result["oos"].empty else [])
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
            from local_store import LocalStore

            daily = LocalStore(db_path).load_daily(
                ts_codes=universe, start=load_start, end=windows["oos_end"]
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
                ),
                "ma20_60": ma_cross_baseline(
                    daily,
                    hold_days=hold_days,
                    max_trades=requested_trades,
                    entry_start=windows["oos_start"],
                    entry_end=windows["oos_end"],
                    codes=universe,
                    portfolio_policy=portfolio_policy,
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
    )
    state["gate"] = gate
    state["anti_overfit"] = anti_overfit
    phase_cb("REPORT", 97, "生成一页可信报告", state)
    n_trials = max(1, len(state.get("is_all") or []))
    report = {
        "research_run_id": research_run_id,
        **gate,
        "versions": {
            "dataset": dataset_version,
            "code": code_version,
            "cost": COST_VERSION,
            "entry": report_entry_fingerprint(_active_entry_definition_id()),
            "portfolio": portfolio_identity["config_hash"],
        },
        "portfolio_model": portfolio_identity,
        "sample": {"universe_size": len(universe), "windows": windows, "step": step},
        "cost_assumptions": COST_ASSUMPTIONS,
        "primary_is": primary_is,
        "primary_oos": primary_oos or None,
        "wf_windows": state.get("wf_windows") or [],
        "baselines": state.get("baselines") or {},
        "sensitivity": frozen["sensitivity"],
        "anti_overfit": anti_overfit,
        "multiple_comparison": {
            "grid_trials": n_trials,
            "correction": "none",
            "note": (
                f"网格共 {n_trials} 组参数按 IS 选优，未做 PBO / Deflated Sharpe / Bonferroni 校正；"
                "Top 组合的 OOS 结果应按试验次数打折解读，必须结合三窗 WF 与双基线共同判断，"
                "单一 OOS 良好不足以证明 edge。"
            ),
        },
    }
    report["markdown"] = render_trusted_report(report)
    # P3.2：v2 正式统计（DSR/MinTRL）——对 primary 组合在 OOS 窗逐笔回放取净收益；
    # 样本不足/异常 → INSUFFICIENT（不伪造）
    try:
        from ab_screener.research.backtest_engine import run_single_backtest
        from ab_screener.research.validation import v2_statistics_block

        if primary_is:
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
                    n_trials=n_trials,
                )
        else:
            report["v2_statistics"] = {
                "status": "INSUFFICIENT",
                "reason": "无 primary 组合可回放",
            }
    except Exception:  # noqa: BLE001
        report["v2_statistics"] = {"status": "INSUFFICIENT", "reason": "无法计算 v2 统计"}
    state["report"] = report
    phase_cb("CANDIDATE", 99, "门禁完成，准备隔离候选", state)
    return {
        "report": state["report"],
        "is_top": (state.get("is_all") or [])[:12],
        "is_all": (state.get("is_all") or [])[:40],
        "oos": state.get("oos_all") or [],
        "msg": state.get("run_message"),
        "run_mode": run_mode,
        "research_mode": windows.get("mode"),
        "can_claim_edge": gate["candidate_eligible"],
        "gross": {"note": "毛指标仅供诊断，排名和门禁只读取净成本指标"},
        "net": [
            {key: value for key, value in row.items() if "net_" in key or key in ("strategy", "param_id")}
            for row in (state.get("oos_all") or [])
        ],
        "baselines": state.get("baselines") or {},
        "promotion_checks": gate,
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
