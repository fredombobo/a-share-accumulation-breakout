"""Composable, point-in-time-safe research condition extension points.

Conditions are filters attached to the accumulation-breakout entry/exit pipeline;
they are not standalone strategies.  The initial chip plugin is deliberately
experimental and disabled by default until its economic rule is preregistered.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ConditionContext:
    ts_code: str
    signal_date: str
    signal_at: str
    evidence: dict[str, Any] | None


@dataclass(frozen=True)
class ConditionResult:
    passed: bool
    score: float | None
    evidence: dict[str, Any]
    reason: str


class ResearchConditionPlugin(Protocol):
    plugin_id: str
    version: str
    title: str
    required_datasets: tuple[str, ...]
    parameter_schema: ClassVar[dict[str, Any]]
    default_enabled: bool
    production_ready: bool

    def evaluate(self, context: ConditionContext, params: dict[str, Any]) -> ConditionResult: ...


class ChipCostConcentrationV1:
    """Reference evaluator for the future chip-cost entry/exit condition.

    Its formula is executable for unit validation, but ``production_ready`` is
    false: users must preregister the final economic rule before a grid run may
    enable it.  This prevents a placeholder formula becoming hidden curve fit.
    """

    plugin_id: str = "chip_cost_concentration_v1"
    version: str = "0.1.0-experimental"
    title: str = "筹码成本集中度（待预登记）"
    required_datasets: tuple[str, ...] = ("cyq_history",)
    default_enabled: bool = False
    production_ready: bool = False
    parameter_schema: ClassVar[dict[str, Any]] = {
        "max_cost_band_pct": {
            "type": "number",
            "minimum": 0.01,
            "maximum": 1.0,
            "default": 0.20,
            "description": "(cost_85pct-cost_15pct)/weight_avg 的上限",
        },
        "min_winner_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "description": "获利盘比例下限；最终经济边界尚待预登记",
        },
    }

    def evaluate(self, context: ConditionContext, params: dict[str, Any]) -> ConditionResult:
        row = context.evidence
        if not row:
            return ConditionResult(False, None, {}, "缺少信号时点可用的筹码数据")
        available_at = str(row.get("available_at") or "")
        if not available_at or _as_dt(available_at) > _as_dt(context.signal_at):
            return ConditionResult(
                False,
                None,
                {"available_at": available_at, "signal_at": context.signal_at},
                "筹码数据在信号时点尚不可用",
            )
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return ConditionResult(False, None, {}, "筹码数据载荷无效")
        try:
            cost15 = float(payload["cost_15pct"])
            cost85 = float(payload["cost_85pct"])
            weight_avg = float(payload["weight_avg"])
            winner_rate = float(payload["winner_rate"])
        except (KeyError, TypeError, ValueError):
            return ConditionResult(False, None, payload, "筹码字段不完整")
        if weight_avg <= 0 or cost85 < cost15:
            return ConditionResult(False, None, payload, "筹码成本分位关系无效")
        band = (cost85 - cost15) / weight_avg
        max_band = float(params.get("max_cost_band_pct", 0.20))
        min_winner = float(params.get("min_winner_rate", 0.0))
        passed = band <= max_band and winner_rate >= min_winner
        return ConditionResult(
            passed,
            round(max(0.0, 1.0 - band), 6),
            {
                "trade_date": row.get("trade_date"),
                "available_at": available_at,
                "source": row.get("source"),
                "revision": row.get("revision"),
                "cost_band_pct": round(band, 6),
                "winner_rate": winner_rate,
            },
            "筹码条件通过" if passed else "筹码集中度或获利盘比例未通过",
        )


_PLUGINS: dict[str, ResearchConditionPlugin] = {
    ChipCostConcentrationV1.plugin_id: ChipCostConcentrationV1(),
}


def condition_catalog(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    dataset = chip_dataset_status(db_path) if db_path else None
    return [
        {
            "id": plugin.plugin_id,
            "version": plugin.version,
            "title": plugin.title,
            "required_datasets": list(plugin.required_datasets),
            "parameter_schema": plugin.parameter_schema,
            "default_enabled": plugin.default_enabled,
            "production_ready": plugin.production_ready,
            "status": "待预登记，不可加入正式网格" if not plugin.production_ready else "可用",
            "dataset": dataset if "cyq_history" in plugin.required_datasets else None,
        }
        for plugin in _PLUGINS.values()
    ]


def resolve_enabled_conditions(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise TypeError("conditions 必须是数组")
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("condition 每项必须是对象")
        plugin_id = str(item.get("id") or "")
        plugin = _PLUGINS.get(plugin_id)
        if plugin is None:
            raise ValueError(f"未知条件插件: {plugin_id}")
        if bool(item.get("enabled")) and not plugin.production_ready:
            raise ValueError(f"{plugin.title} 尚未完成经济机制预登记，不能加入回测")
        if bool(item.get("enabled")):
            normalized.append({"id": plugin_id, "version": plugin.version, "params": item.get("params") or {}})
    return normalized


def load_chip_evidence(
    db_path: str | Path,
    *,
    ts_code: str,
    signal_date: str,
    signal_at: str,
) -> dict[str, Any] | None:
    """Read the latest chip row that was genuinely available at signal time."""
    path = Path(db_path).resolve()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cyq_history'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            "SELECT trade_date,available_at,source,revision,payload_json "
            "FROM cyq_history WHERE ts_code=? AND trade_date<=? AND available_at<=? "
            "ORDER BY trade_date DESC,revision DESC LIMIT 1",
            (ts_code, signal_date, signal_at),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result["payload"] = json.loads(str(result.pop("payload_json")))
    except (TypeError, ValueError):
        result["payload"] = None
    return result


def evaluate_condition(
    db_path: str | Path,
    *,
    plugin_id: str,
    ts_code: str,
    signal_date: str,
    signal_at: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin = _PLUGINS.get(plugin_id)
    if plugin is None:
        raise ValueError(f"未知条件插件: {plugin_id}")
    evidence = (
        load_chip_evidence(
            db_path, ts_code=ts_code, signal_date=signal_date, signal_at=signal_at
        )
        if "cyq_history" in plugin.required_datasets
        else None
    )
    result = plugin.evaluate(
        ConditionContext(ts_code, signal_date, signal_at, evidence), params or {}
    )
    return asdict(result)


def chip_dataset_status(db_path: str | Path | None) -> dict[str, Any]:
    if db_path is None:
        return {"available": False, "rows": 0, "reason": "未提供数据库"}
    path = Path(db_path).resolve()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cyq_history'"
        ).fetchone()
        if not exists:
            return {"available": False, "rows": 0, "reason": "缺少 cyq_history"}
        row = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT ts_code),MIN(trade_date),MAX(trade_date),"
            "SUM(CASE WHEN available_at IS NULL OR source IS NULL THEN 1 ELSE 0 END) "
            "FROM cyq_history"
        ).fetchone()
    assert row is not None
    return {
        "available": int(row[0]) > 0 and int(row[4]) == 0,
        "rows": int(row[0]),
        "codes": int(row[1]),
        "earliest": row[2],
        "latest": row[3],
        "invalid_lineage_rows": int(row[4]),
    }


def _as_dt(value: str) -> datetime:
    raw = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TZ)
    return parsed.astimezone(_TZ)
