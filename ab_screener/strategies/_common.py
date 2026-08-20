"""插件通用辅助：K 线列归一化、观察构造、配置加载。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.strategies.contracts import (
    SignalObservation,
    StrategySpec,
    config_hash,
    strategy_hash,
)


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """列归一化：date/vol/amount 别名统一；按日期升序。"""
    out = df.rename(
        columns={
            "date": "date", "volume": "vol", "amount": "amount",
            "pre_close": "pre_close",
        }
    ).copy()
    out = out.sort_values("date").reset_index(drop=True)
    if "close" not in out.columns or "open" not in out.columns:
        raise ValueError("K 线缺少 open/close 列")
    return out


def build_observation(
    spec: StrategySpec,
    *,
    ts_code: str,
    signal_date: str,
    snapshot_id: str,
    input_hash: str,
    config: dict[str, Any],
    payload: dict[str, Any],
    explanation: str,
    tradeable: bool,
) -> SignalObservation:
    return SignalObservation(
        strategy_definition_id=spec.strategy_definition_id,
        strategy_hash=strategy_hash(spec),
        input_hash=input_hash,
        snapshot_id=snapshot_id,
        ts_code=ts_code,
        signal_date=str(signal_date),
        config_hash=config_hash(config),
        payload=payload,
        explanation=explanation,
        tradeable=tradeable,
    )


def plugin_spec(
    strategy_definition_id: str,
    version: str,
    assumption: str,
    failure: str,
    fixture: str,
    config_path: str = "",
) -> StrategySpec:
    return StrategySpec(
        strategy_definition_id=strategy_definition_id,
        version=version,
        economic_assumption=assumption,
        failure_conditions=failure,
        pit_test="signal_date 取最后收盘 bar；不使用当日之后数据（防未来函数）",
        golden_fixture=fixture,
        config_path=config_path,
    )
