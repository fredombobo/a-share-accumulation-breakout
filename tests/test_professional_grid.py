from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ab_screener.research.condition_plugins import (
    evaluate_condition,
    resolve_enabled_conditions,
)
from ab_screener.research.professional_grid import (
    LONG_RUNNING_WARNING_COMBINATIONS,
    MAX_COMBINATIONS,
    ProfessionalGridError,
    expand_parameter_space,
    resolve_universe,
    validate_fixed_parameters,
)
from local_store import LocalStore

_TZ = ZoneInfo("Asia/Shanghai")


def test_default_grid_is_multi_parameter_deterministic_and_supports_200_days() -> None:
    first = expand_parameter_space(None)
    second = expand_parameter_space(None)

    assert first["count"] == 432
    assert first["sha256"] == second["sha256"]
    assert first["values"]["box_max_days"] == [60, 80, 100, 120, 140, 160, 180, 200]
    assert first["values"]["breakout_vol_ratio"] == [1.4, 1.6, 1.8]
    assert first["values"]["target_pct"] == [0.1, 0.12, 0.15]
    assert first["long_running"] is False
    assert first["horizon"] >= 265


def test_grid_rejects_oversized_space_and_filters_invalid_box_bounds() -> None:
    with pytest.raises(ProfessionalGridError) as caught:
        expand_parameter_space(
            {
                "box_min_days": {"mode": "range", "start": 20, "stop": 200, "step": 10},
                "box_max_days": {"mode": "range", "start": 40, "stop": 240, "step": 10},
                "breakout_vol_ratio": {"mode": "range", "start": 1.0, "stop": 4.0, "step": 0.1},
            }
        )
    assert caught.value.code == "COMBINATION_LIMIT_EXCEEDED"

    small = expand_parameter_space(
        {
            "box_min_days": {"mode": "values", "values": [60, 100]},
            "box_max_days": {"mode": "fixed", "value": 80},
            "breakout_vol_ratio": {"mode": "fixed", "value": 1.6},
            "stop_pct": {"mode": "fixed", "value": 0.07},
            "exit_window": {"mode": "fixed", "value": 10},
        }
    )
    assert {item["box_min_days"] for item in small["signal_combinations"]} == {60}
    assert small["invalid_signal_combinations"] == 1


def test_grid_allows_ten_times_budget_with_warning_then_rejects_hard_cap() -> None:
    long_running = expand_parameter_space(
        {
            "breakout_vol_ratio": {"mode": "values", "values": [1.2, 1.4, 1.6, 1.8]},
        }
    )
    assert LONG_RUNNING_WARNING_COMBINATIONS == 512
    assert MAX_COMBINATIONS == 5_120
    assert long_running["count"] == 576
    assert long_running["long_running"] is True

    with pytest.raises(ProfessionalGridError) as caught:
        expand_parameter_space(
            {
                "box_min_days": {"mode": "range", "start": 20, "stop": 200, "step": 10},
                "box_max_days": {"mode": "range", "start": 40, "stop": 240, "step": 10},
                "breakout_vol_ratio": {"mode": "range", "start": 1.0, "stop": 4.0, "step": 0.1},
            }
        )
    assert caught.value.code == "COMBINATION_LIMIT_EXCEEDED"
    assert caught.value.details["limit"] == 5_120


def test_manual_fixed_parameters_reuse_grid_validation() -> None:
    first = expand_parameter_space(None)["combinations"][0]
    values = {**first["signal"], **first["exit"]}
    validated = validate_fixed_parameters(values)
    assert validated == first

    with pytest.raises(ProfessionalGridError) as missing:
        validate_fixed_parameters({"box_min_days": 60})
    assert missing.value.code == "MISSING_MANUAL_PARAMETER"

    with pytest.raises(ProfessionalGridError) as invalid_target:
        validate_fixed_parameters({**values, "target_pct": 1.01})
    assert invalid_target.value.code == "PARAMETER_OUT_OF_RANGE"


def test_current_industry_universe_is_frozen_with_stable_hash(tmp_path: Path) -> None:
    db = tmp_path / "universe.db"
    LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO stock_basic(ts_code,name,industry) VALUES (?,?,?)",
            [(f"{index:06d}.SZ", f"股票{index}", "半导体" if index < 20 else "机械") for index in range(40)],
        )

    one = resolve_universe(db, industries=["半导体"], max_codes=600)
    two = resolve_universe(db, industries=["半导体"], max_codes=600)

    assert one["count"] == 20
    assert one["sha256"] == two["sha256"]
    assert one["classification_mode"] == "CURRENT_CLASSIFICATION_FROZEN_UNIVERSE"


def test_universe_can_freeze_market_and_area_groups(tmp_path: Path) -> None:
    db = tmp_path / "classification-universe.db"
    LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO stock_basic(ts_code,name,industry,market,area) VALUES (?,?,?,?,?)",
            [
                (
                    f"{index:06d}.SZ",
                    f"股票{index}",
                    "软件服务" if index < 30 else "专用机械",
                    "创业板" if index < 30 else "主板",
                    "广东" if index % 2 == 0 else "北京",
                )
                for index in range(50)
            ],
        )

    market = resolve_universe(
        db,
        classification="market",
        groups=["创业板"],
        max_codes=600,
    )
    area = resolve_universe(
        db,
        classification="area",
        groups=["广东"],
        max_codes=600,
    )

    assert market["classification"] == "market"
    assert market["groups"] == ["创业板"]
    assert market["count"] == 30
    assert area["classification"] == "area"
    assert area["groups"] == ["广东"]
    assert area["count"] == 25

    with pytest.raises(ProfessionalGridError) as caught:
        resolve_universe(db, classification="concept", max_codes=600)
    assert caught.value.code == "UNKNOWN_CLASSIFICATION"


def test_chip_plugin_is_default_off_and_pit_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="尚未完成经济机制预登记"):
        resolve_enabled_conditions([{"id": "chip_cost_concentration_v1", "enabled": True}])

    db = tmp_path / "chip.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE cyq_history(ts_code TEXT,trade_date TEXT,revision INTEGER,"
            "available_at TEXT,source TEXT,payload_json TEXT)"
        )
        conn.execute(
            "INSERT INTO cyq_history VALUES (?,?,?,?,?,?)",
            (
                "000001.SZ",
                "20260105",
                1,
                "2026-01-06T09:00:00+08:00",
                "tushare",
                '{"cost_15pct":9.5,"cost_85pct":10.5,"weight_avg":10,"winner_rate":0.6}',
            ),
        )

    before_available = evaluate_condition(
        db,
        plugin_id="chip_cost_concentration_v1",
        ts_code="000001.SZ",
        signal_date="20260105",
        signal_at="2026-01-05T15:00:00+08:00",
    )
    after_available = evaluate_condition(
        db,
        plugin_id="chip_cost_concentration_v1",
        ts_code="000001.SZ",
        signal_date="20260106",
        signal_at=datetime(2026, 1, 6, 15, 0, tzinfo=_TZ).isoformat(),
    )
    assert before_available["passed"] is False
    assert before_available["reason"] == "缺少信号时点可用的筹码数据"
    assert after_available["passed"] is True
