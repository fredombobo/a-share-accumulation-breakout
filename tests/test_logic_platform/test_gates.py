"""闸门测试：gated / rejected / draft 状态流转。"""
from __future__ import annotations

from logic_platform.backtest.gates import GateConfig, evaluate


def _metrics(n=40, wr=0.5, pf=1.5, mdd=0.2, ar=0.03) -> dict:
    return {
        "n_trades": n, "win_rate": wr, "profit_factor": pf,
        "max_drawdown": mdd, "avg_ret": ar, "median_ret": ar,
        "total_return": ar * n, "avg_hold_days": 10, "exits": {"stop": 20, "target": 20},
    }


def test_all_pass_gated():
    g = evaluate(_metrics())
    assert g.status == "gated"
    assert g.passed is True
    assert all(c.passed for c in g.checks)


def test_too_few_trades_draft():
    g = evaluate(_metrics(n=10))
    assert g.status == "draft"
    assert g.passed is False
    assert not any(c.rule == "min_trades" and c.passed for c in g.checks)


def test_high_drawdown_rejected():
    g = evaluate(_metrics(mdd=0.6))
    assert g.status == "rejected"
    assert not any(c.rule == "max_drawdown" and c.passed for c in g.checks)


def test_low_win_rate_rejected():
    g = evaluate(_metrics(wr=0.3))
    assert g.status == "rejected"
    assert not any(c.rule == "min_win_rate" and c.passed for c in g.checks)


def test_custom_config_relax():
    g = evaluate(_metrics(n=10, wr=0.3), GateConfig(min_trades=5, min_win_rate=0.25))
    assert g.status == "gated"
    assert g.passed is True


def test_empty_metrics_draft():
    g = evaluate({})
    assert g.status == "draft"
    assert g.passed is False


def test_gate_json_serializable():
    import json

    g = evaluate(_metrics())
    json.dumps(g.to_json(), ensure_ascii=False)
    assert g.to_json()["status"] == "gated"
