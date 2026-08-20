"""P4.1 策略插件契约测试：六插件/契约字段/执行定义引用/异常隔离/overlay 独立。"""
from __future__ import annotations

import pandas as pd
import pytest

from ab_screener.regimes.contracts import OverlayDecision, OverlayInput
from ab_screener.regimes.defensive_overlay_v1 import evaluate as overlay_evaluate
from ab_screener.strategies import (
    NEXT_TRADABLE_OPEN_EXECUTION_V1,
)
from ab_screener.strategies.contracts import StrategySpec, observation_id_for
from ab_screener.strategies.registry import (
    StrategyRegistryError,
    register_selection_plugin,
    require_six_selection_plugins,
    run_all_selection_plugins,
    selection_plugin_ids,
    selection_plugins,
)


def _bars(rows: int = 100) -> pd.DataFrame:
    rng = pd.date_range("2026-01-01", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "date": rng.strftime("%Y%m%d"),
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2,
            "vol": 100_000, "amount": 1e8,
        }
    )


def test_exactly_six_selection_plugins():
    import ab_screener.strategies  # noqa: F401  导入即注册

    ids = selection_plugin_ids()
    assert len(ids) == 6
    assert set(ids) == {
        "accumulation_breakout_v1", "volatility_contraction_v1",
        "trend_pullback_v1", "platform_breakout_v1",
        "oversold_reversal_v1", "relative_strength_high_v1",
    }
    require_six_selection_plugins()  # 契约断言


def test_every_plugin_has_full_contract_fields():
    import ab_screener.strategies  # noqa: F401

    for plugin_id, entry in selection_plugins().items():
        spec = entry["spec"]
        assert spec.strategy_definition_id == plugin_id
        assert spec.economic_assumption.strip()
        assert spec.failure_conditions.strip()
        assert spec.pit_test.strip()
        assert spec.golden_fixture.strip()
        assert spec.research_status in (
            "EXPERIMENTAL", "REJECTED", "CANDIDATE", "SHADOW",
            "ACTIVE_FOR_A_POOL", "RETIRED",
        )
        assert spec.config_path.startswith("configs/strategies/")


def test_observations_share_execution_definition():
    """选择定义与执行时点分离：所有观察引用 NEXT_TRADABLE_OPEN_EXECUTION_V1。"""
    import ab_screener.strategies  # noqa: F401

    for plugin_id, entry in selection_plugins().items():
        observations = entry["detect"](
            _bars(), None, ts_code="000001.SZ", snapshot_id="snap1", input_hash="h1"
        )
        if observations:
            assert all(o.entry_definition_id == NEXT_TRADABLE_OPEN_EXECUTION_V1
                       for o in observations)
            assert all(o.strategy_definition_id == plugin_id for o in observations)


def test_observation_id_deterministic():
    a = observation_id_for(strategy_definition_id="s1", ts_code="000001.SZ",
                           signal_date="20260810", snapshot_id="snap1", input_hash="h1")
    b = observation_id_for(strategy_definition_id="s1", ts_code="000001.SZ",
                           signal_date="20260810", snapshot_id="snap1", input_hash="h1")
    c = observation_id_for(strategy_definition_id="s1", ts_code="000001.SZ",
                           signal_date="20260810", snapshot_id="snap1", input_hash="h2")
    assert a == b != c


def test_duplicate_registration_rejected():
    spec = StrategySpec(
        strategy_definition_id="dup_plugin", version="v1",
        economic_assumption="x", failure_conditions="y",
        pit_test="z", golden_fixture="f",
    )
    register_selection_plugin(spec, lambda *a, **kw: [])
    with pytest.raises(StrategyRegistryError, match="重复注册"):
        register_selection_plugin(spec, lambda *a, **kw: [])


def test_exception_isolation_between_plugins(monkeypatch):
    """一个插件抛异常 → 其他插件继续产出观察。"""
    import ab_screener.strategies  # noqa: F401
    from ab_screener.strategies import registry as reg

    def boom(bars, config, **kw):
        raise RuntimeError("plugin crash")

    monkeypatch.setitem(reg._SELECTION, "oversold_reversal_v1", {"spec": None, "detect": boom})
    results = run_all_selection_plugins(
        _bars(), ts_code="000001.SZ", snapshot_id="s", input_hash="h"
    )
    assert "error" in results["oversold_reversal_v1"]
    others = {k: v for k, v in results.items() if k != "oversold_reversal_v1"}
    assert all(isinstance(v, list) for v in others.values())


def test_regime_overlay_does_not_produce_signal_observations():
    """防守 overlay 只改变开仓许可；不实现 SignalObservation producer。"""
    import ab_screener.regimes  # noqa: F401
    from ab_screener.regimes.registry import regime_overlays

    assert len(regime_overlays()) == 1
    entry = regime_overlays()["defensive_overlay_v1"]
    assert "detect" not in entry          # 不实现 producer
    decision = overlay_evaluate(
        OverlayInput(market_regime="defensive", benchmark_trend=-0.1,
                     drawdown_from_peak=0.05)
    )
    assert isinstance(decision, OverlayDecision)
    assert decision.allow_new_entries is False
    assert decision.mode == "defensive"
    # 人工覆盖优先
    manual = overlay_evaluate(
        OverlayInput(market_regime="defensive", benchmark_trend=-0.1,
                     drawdown_from_peak=0.05, allow_new_entries_override=True)
    )
    assert manual.allow_new_entries is True
