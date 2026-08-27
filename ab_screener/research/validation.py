"""Deterministic, net-cost research promotion gate.

The functions in this module are deliberately independent from FastAPI and the
database so the exact promotion contract can be tested with fixed fixtures.
"""

from __future__ import annotations

import math
from typing import Any

_PARAM_KEYS = ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _check(check_id: str, label: str, passed: bool, actual: Any, threshold: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "passed": bool(passed),
        "actual": actual,
        "threshold": threshold,
    }


def _candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    param = row.get("param_id") or row.get("oos_param_id")
    if param:
        return ("param_id", str(param))
    return ("params", *(row.get(key) for key in _PARAM_KEYS))


def evaluate_personal_anti_overfit(
    *,
    is_candidates: list[dict[str, Any]],
    oos_candidates: list[dict[str, Any]],
    wf_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate frozen-winner stability without using OOS to replace the winner.

    This is an explicit personal research gate, not a claim of a full CSCV/PBO
    implementation.  It tests trial multiplicity, IS-to-OOS degradation,
    OOS rank inversion, nearby-parameter stability and WF consistency.
    """
    version = "personal-anti-overfit-v1"
    reasons: list[str] = []
    checks: list[dict[str, Any]] = []
    if not is_candidates or len(oos_candidates) < 3 or len(wf_windows) != 3:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "version": version,
            "checks": [],
            "block_reasons": ["反过拟合证据需要完整参数网格、至少三个冻结候选OOS结果和三个WF窗口"],
        }

    primary_is = is_candidates[0]
    primary_key = _candidate_key(primary_is)
    primary_oos = next(
        (row for row in oos_candidates if _candidate_key(row) == primary_key),
        None,
    )
    if primary_oos is None:
        param_tuple = tuple(primary_is.get(key) for key in _PARAM_KEYS)
        primary_oos = next(
            (row for row in oos_candidates if tuple(row.get(key) for key in _PARAM_KEYS) == param_tuple),
            None,
        )
    is_pf = _number(primary_is.get("net_profit_factor"))
    oos_pf = _number(primary_oos.get("oos_net_profit_factor")) if primary_oos else None
    ranked = [(row, _number(row.get("oos_net_avg_return"))) for row in oos_candidates]
    wf_pfs = [_number(row.get("test_pf")) for row in wf_windows]
    neighbor_pfs = [
        _number(row.get("oos_net_profit_factor")) for row in oos_candidates if row is not primary_oos
    ]
    complete = (
        is_pf is not None
        and is_pf > 0
        and oos_pf is not None
        and all(value is not None for _, value in ranked)
        and len(neighbor_pfs) >= 2
        and all(value is not None for value in neighbor_pfs)
        and all(value is not None for value in wf_pfs)
    )
    if not complete:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "version": version,
            "checks": [],
            "block_reasons": ["反过拟合净成本指标不完整"],
        }

    assert is_pf is not None and oos_pf is not None
    ordered = sorted(
        ((row, value if value is not None else 0.0) for row, value in ranked),
        key=lambda item: item[1],
        reverse=True,
    )
    primary_rank = next(index for index, (row, _) in enumerate(ordered, 1) if row is primary_oos)
    retention = oos_pf / is_pf
    neighbor_values = [value if value is not None else 0.0 for value in neighbor_pfs]
    profitable_neighbors = len([value for value in neighbor_values if value >= 1.0])
    neighbor_ratio = profitable_neighbors / len(neighbor_pfs)
    wf_values = [value if value is not None else 0.0 for value in wf_pfs]
    profitable_wf = len([value for value in wf_values if value >= 1.0])
    checks.extend(
        [
            _check("anti_trials", "参数试验数量", len(is_candidates) >= 30, len(is_candidates), ">= 30"),
            _check(
                "anti_oos_candidates",
                "冻结候选OOS复核数量",
                len(oos_candidates) >= 3,
                len(oos_candidates),
                ">= 3",
            ),
            _check(
                "anti_pf_retention", "IS到OOS净PF保持率", retention >= 0.75, round(retention, 4), ">= 0.75"
            ),
            _check(
                "anti_oos_rank",
                "冻结IS第一名的OOS排名",
                primary_rank <= math.ceil(len(ordered) / 2),
                {"rank": primary_rank, "evaluated": len(ordered)},
                "位于前半",
            ),
            _check(
                "anti_neighbor_stability",
                "相邻优选参数OOS盈利稳定性",
                neighbor_ratio >= 0.5,
                round(neighbor_ratio, 4),
                ">= 50% 的邻近参数净PF>=1",
            ),
            _check(
                "anti_wf_consistency",
                "WF窗口盈利一致性",
                profitable_wf >= 2,
                {"profitable": profitable_wf, "windows": 3},
                ">= 2/3 窗口净PF>=1",
            ),
        ]
    )
    for item in checks:
        if not item["passed"]:
            reasons.append(f"{item['label']}未通过（实际 {item['actual']}，要求 {item['threshold']}）")
    verdict = "PASS" if not reasons else "FAIL"
    return {
        "verdict": verdict,
        "version": version,
        "checks": checks,
        "block_reasons": reasons,
        "metrics": {
            "trial_count": len(is_candidates),
            "oos_candidate_count": len(oos_candidates),
            "pf_retention": round(retention, 6),
            "primary_oos_rank": primary_rank,
            "neighbor_profitable_ratio": round(neighbor_ratio, 6),
            "wf_profitable_windows": profitable_wf,
        },
    }


def v2_statistics_block(
    oos_returns: list[float],
    *,
    n_trials: int,
    trial_sharpe_std: float | None = None,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Compute DSR/MinTRL from OOS daily portfolio returns; missing trials fail closed."""
    from ab_screener.research.deflated_sharpe import (
        deflated_sharpe,
        expected_max_sharpe_null,
    )
    from ab_screener.research.min_track_record import min_track_record_length

    finite = [float(r) for r in oos_returns if r is not None and math.isfinite(float(r))]
    if len(finite) < 30:
        return {
            "status": "INSUFFICIENT",
            "reason": f"OOS 逐笔收益样本不足（{len(finite)} < 30），无法计算 v2 正式统计",
        }
    mean = sum(finite) / len(finite)
    var = sum((r - mean) ** 2 for r in finite) / (len(finite) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std <= 0:
        return {"status": "INSUFFICIENT", "reason": "OOS 收益零方差，无法计算 Sharpe"}
    if n_trials > 1 and (
        trial_sharpe_std is None
        or not math.isfinite(float(trial_sharpe_std))
        or float(trial_sharpe_std) <= 0
    ):
        return {
            "status": "INSUFFICIENT",
            "reason": "多试验 DSR 缺少 IS 参数 Sharpe 横截面标准差",
        }
    sharpe = mean / std
    skew = 0.0
    kurt = 3.0
    if len(finite) >= 30:
        m3 = sum((r - mean) ** 3 for r in finite) / len(finite)
        m4 = sum((r - mean) ** 4 for r in finite) / len(finite)
        if std > 0:
            skew = m3 / (std**3)
            kurt = m4 / (std**4)
    sr0 = expected_max_sharpe_null(n_trials, sharpe_std=trial_sharpe_std)
    dsr = deflated_sharpe(sharpe, len(finite), skew, kurt, n_trials, sr0=sr0)
    mintrl = min_track_record_length(sharpe, skew, kurt, confidence=confidence)
    return {
        "status": "OK",
        "n_periods": len(finite),
        "sharpe_period": round(sharpe, 6),
        "skew": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "dsr": round(dsr, 6),
        "dsr_pass_95": dsr >= 0.95,
        "sr0_null_max": round(sr0, 6),
        "trial_sharpe_std": trial_sharpe_std,
        "min_track_record_length": round(mintrl, 2),
        "min_track_record_coverage": round(len(finite) / mintrl, 3) if mintrl > 0 else None,
        "n_trials": n_trials,
    }


def evaluate_trusted_gate(
    *,
    research_mode: str,
    automatic_window: bool,
    run_mode: str,
    oos: dict[str, Any],
    wf_windows: list[dict[str, Any]],
    baselines: dict[str, Any],
    anti_overfit: dict[str, Any] | None = None,
    portfolio_model: dict[str, str] | None = None,
    pit_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return PASS/FAIL/INSUFFICIENT_EVIDENCE for one frozen IS winner.

    Missing evidence is never coerced to zero.  This distinction matters: a
    complete but weak result is FAIL, while unavailable validation evidence is
    INSUFFICIENT_EVIDENCE.
    """
    reasons: list[str] = []
    checks: list[dict[str, Any]] = []

    trusted_scope = research_mode == "full" and automatic_window and run_mode == "grid"
    checks.append(
        _check(
            "trusted_scope",
            "自动 full 窗格搜索",
            trusted_scope,
            {"research_mode": research_mode, "automatic_window": automatic_window, "run_mode": run_mode},
            "full + 自动窗口 + grid",
        )
    )
    if not trusted_scope:
        reasons.append("只有自动 full 窗格搜索可形成可信结论；当前运行仅供探索")

    oos_n = _number(oos.get("oos_net_n_trades"))
    oos_pf = _number(oos.get("oos_net_profit_factor"))
    oos_wr = _number(oos.get("oos_net_win_rate"))
    oos_dd = _number(oos.get("oos_net_max_drawdown"))
    candidate_return = _number(oos.get("oos_net_avg_return"))
    oos_complete = all(value is not None for value in (oos_n, oos_pf, oos_wr, oos_dd, candidate_return))
    if not oos_complete:
        reasons.append("OOS 净成本指标不完整")
    else:
        assert oos_n is not None and oos_pf is not None and oos_wr is not None and oos_dd is not None
        oos_checks = [
            _check("oos_trades", "OOS 净交易数", oos_n >= 30, int(oos_n), ">= 30"),
            _check("oos_pf", "OOS 净 PF", oos_pf >= 1.0, oos_pf, ">= 1.0"),
            _check("oos_win_rate", "OOS 净胜率", oos_wr >= 0.30, oos_wr, ">= 30%"),
            _check("oos_drawdown", "OOS 净最大回撤", oos_dd <= 0.25, oos_dd, "<= 25%"),
        ]
        checks.extend(oos_checks)
        for item in oos_checks:
            if not item["passed"]:
                reasons.append(f"{item['label']}未通过（实际 {item['actual']}，要求 {item['threshold']}）")

    wf_complete = len(wf_windows) == 3
    train_pfs: list[float] = []
    test_pfs: list[float] = []
    if wf_complete:
        for index, row in enumerate(wf_windows, 1):
            train_pf = _number(row.get("train_pf"))
            test_pf = _number(row.get("test_pf"))
            test_dd = _number(row.get("test_dd"))
            test_n = _number(row.get("test_n"))
            if any(value is None for value in (train_pf, test_pf, test_dd, test_n)):
                wf_complete = False
                break
            assert train_pf is not None and test_pf is not None and test_dd is not None and test_n is not None
            train_pfs.append(train_pf)
            test_pfs.append(test_pf)
            trade_check = _check(
                f"wf{index}_trades", f"WF{index} 净交易数", test_n >= 30, int(test_n), ">= 30"
            )
            dd_check = _check(
                f"wf{index}_drawdown", f"WF{index} 净最大回撤", test_dd <= 0.25, test_dd, "<= 25%"
            )
            checks.extend((trade_check, dd_check))
            for item in (trade_check, dd_check):
                if not item["passed"]:
                    reasons.append(
                        f"{item['label']}未通过（实际 {item['actual']}，要求 {item['threshold']}）"
                    )
    if not wf_complete:
        reasons.append("三个 WF 窗口的净成本结果不完整")
    else:
        train_mean = sum(train_pfs) / 3
        test_mean = sum(test_pfs) / 3
        stability = _check(
            "wf_stability",
            "WF 测试/训练净 PF 稳定性",
            train_mean > 0 and test_mean >= 0.8 * train_mean,
            {"train_mean": train_mean, "test_mean": test_mean},
            "test_mean >= 80% * train_mean",
        )
        checks.append(stability)
        if not stability["passed"]:
            reasons.append("WF 测试窗平均净 PF 低于训练窗平均净 PF 的 80%")

    baseline_values: dict[str, float | None] = {}
    for key in ("random", "ma20_60"):
        baseline_row = baselines.get(key)
        baseline_values[key] = (
            _number(baseline_row.get("net_avg_return")) if isinstance(baseline_row, dict) else None
        )
    baseline_complete = candidate_return is not None and all(v is not None for v in baseline_values.values())
    if not baseline_complete:
        reasons.append("随机与 MA20/60 基线净收益证据不完整")
    else:
        assert candidate_return is not None
        labels = {"random": "固定种子随机基线", "ma20_60": "MA20/60 基线"}
        for key in ("random", "ma20_60"):
            value = baseline_values[key]
            assert value is not None
            item = _check(
                f"beat_{key}",
                f"主候选优于{labels[key]}",
                candidate_return > value,
                {"candidate": candidate_return, "baseline": value},
                "candidate portfolio total return > baseline",
            )
            checks.append(item)
            if not item["passed"]:
                reasons.append(f"主候选未优于{labels[key]}")

    portfolio_evidence_complete = True
    if portfolio_model is not None:
        expected_version = portfolio_model.get("version")
        expected_hash = portfolio_model.get("config_hash")
        expected_execution = portfolio_model.get("execution_model_version")
        expected_fee = portfolio_model.get("fee_version")
        if not all((expected_version, expected_hash, expected_execution, expected_fee)):
            portfolio_evidence_complete = False
            reasons.append("组合账户模型身份不完整")
        oos_values = (
            oos.get("oos_portfolio_model_version"),
            oos.get("oos_portfolio_config_hash"),
            oos.get("oos_portfolio_execution_model_version"),
            oos.get("oos_portfolio_fee_version"),
            oos.get("oos_portfolio_status"),
        )
        if any(value is None for value in oos_values):
            portfolio_evidence_complete = False
            reasons.append("OOS 组合账户证据不完整")
        else:
            oos_portfolio_check = _check(
                "oos_portfolio_accounting",
                "OOS 共享账户与每日盯市",
                oos_values == (expected_version, expected_hash, expected_execution, expected_fee, "PASS"),
                {
                    "version": oos_values[0],
                    "config_hash": oos_values[1],
                    "execution_model_version": oos_values[2],
                    "fee_version": oos_values[3],
                    "status": oos_values[4],
                },
                "组合/执行/费用版本与配置一致且完整平仓",
            )
            checks.append(oos_portfolio_check)
            if not oos_portfolio_check["passed"]:
                reasons.append("OOS 组合账户未按权威版本完整结算")

        for index, row in enumerate(wf_windows, 1):
            train_status = row.get("train_portfolio_status")
            test_status = row.get("test_portfolio_status")
            if train_status is None or test_status is None:
                portfolio_evidence_complete = False
                reasons.append(f"WF{index} 组合账户证据不完整")
                continue
            wf_portfolio_check = _check(
                f"wf{index}_portfolio_accounting",
                f"WF{index} 共享账户完整结算",
                train_status == "PASS" and test_status == "PASS",
                {"train": train_status, "test": test_status},
                "训练窗与测试窗均 PASS",
            )
            checks.append(wf_portfolio_check)
            if not wf_portfolio_check["passed"]:
                reasons.append(f"WF{index} 组合账户存在未平仓或结算异常")

        for key, label in (("random", "随机基线"), ("ma20_60", "MA20/60 基线")):
            baseline_row = baselines.get(key)
            values = (
                baseline_row.get("portfolio_model_version") if isinstance(baseline_row, dict) else None,
                baseline_row.get("portfolio_config_hash") if isinstance(baseline_row, dict) else None,
                baseline_row.get("portfolio_execution_model_version")
                if isinstance(baseline_row, dict)
                else None,
                baseline_row.get("portfolio_fee_version") if isinstance(baseline_row, dict) else None,
                baseline_row.get("portfolio_status") if isinstance(baseline_row, dict) else None,
            )
            if any(value is None for value in values):
                portfolio_evidence_complete = False
                reasons.append(f"{label}组合账户证据不完整")
                continue
            baseline_portfolio_check = _check(
                f"{key}_portfolio_accounting",
                f"{label}共享账户完整结算",
                values == (expected_version, expected_hash, expected_execution, expected_fee, "PASS"),
                {
                    "version": values[0],
                    "config_hash": values[1],
                    "execution_model_version": values[2],
                    "fee_version": values[3],
                    "status": values[4],
                },
                "组合/执行/费用版本与配置一致且完整平仓",
            )
            checks.append(baseline_portfolio_check)
            if not baseline_portfolio_check["passed"]:
                reasons.append(f"{label}组合账户未按权威版本完整结算")

    pit_evidence_complete = True
    if pit_snapshot is not None:
        required_pit_fields = (
            "version",
            "decision_at",
            "data_start",
            "data_end",
            "universe_size",
            "universe_sha256",
            "dataset_fingerprint",
        )
        pit_evidence_complete = all(pit_snapshot.get(key) for key in required_pit_fields)
        if not pit_evidence_complete:
            reasons.append("PIT 研究快照身份不完整")
        else:
            pit_check = _check(
                "point_in_time_snapshot",
                "冻结 PIT 修订与历史生命周期宇宙",
                pit_snapshot.get("version") == "research-pit-reader-v2.0.0"
                and str(pit_snapshot.get("decision_at")).endswith("+08:00")
                and len(str(pit_snapshot.get("universe_sha256"))) == 64
                and len(str(pit_snapshot.get("dataset_fingerprint"))) == 16,
                pit_snapshot,
                "版本固定、+08:00 截止点、宇宙/数据 hash 完整",
            )
            checks.append(pit_check)
            if not pit_check["passed"]:
                reasons.append("PIT 研究快照版本或指纹非法")

    anti_complete = (
        isinstance(anti_overfit, dict)
        and anti_overfit.get("verdict") in ("PASS", "FAIL")
        and isinstance(anti_overfit.get("checks"), list)
    )
    if not anti_complete:
        reasons.append("反过拟合门禁证据不完整")
    else:
        assert isinstance(anti_overfit, dict)
        checks.extend(anti_overfit.get("checks") or [])
        reasons.extend(str(reason) for reason in anti_overfit.get("block_reasons") or [])

    evidence_complete = (
        trusted_scope
        and oos_complete
        and wf_complete
        and baseline_complete
        and anti_complete
        and portfolio_evidence_complete
        and pit_evidence_complete
    )
    if not evidence_complete:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif all(item["passed"] for item in checks):
        verdict = "PASS"
    else:
        verdict = "FAIL"

    return {
        "verdict": verdict,
        "candidate_eligible": verdict == "PASS",
        "checks": checks,
        "block_reasons": reasons,
        "anti_overfit": anti_overfit,
        "summary": (
            "通过可信门禁，仅允许登记为隔离候选参数，不会自动进入 A 池或下单"
            if verdict == "PASS"
            else "；".join(reasons)
        ),
    }
