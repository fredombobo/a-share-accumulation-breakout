"""Professional multi-parameter grid contract for accumulation-breakout research."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from ab_screener.domain.entry_definition import BREAKOUT_WINDOW_DAYS
from config import (
    BOX_MAX_AMP,
    BREAKOUT_CHG_MAX,
    BREAKOUT_CHG_MIN,
    BREAKOUT_VS_RECENT_VOL_RATIO,
)

MAX_COMBINATIONS = 512
MAX_UNIVERSE_CODES = 1500
GRID_CONTRACT_VERSION = "professional-grid-v1.0.0"


@dataclass(frozen=True)
class ParameterDefinition:
    key: str
    title: str
    group: Literal["signal", "exit"]
    value_type: Literal["integer", "number", "boolean"]
    minimum: float | int | None
    maximum: float | int | None
    default: dict[str, Any]
    description: str


PARAMETERS: tuple[ParameterDefinition, ...] = (
    ParameterDefinition("box_min_days", "横盘最短天数", "signal", "integer", 20, 200,
                        {"mode": "fixed", "value": 60}, "箱体候选至少持续多少个交易日"),
    ParameterDefinition("box_max_days", "横盘最长天数", "signal", "integer", 40, 240,
                        {"mode": "range", "start": 60, "stop": 200, "step": 20},
                        "按 60~200 天等区间步进搜索；必须不小于最短天数"),
    ParameterDefinition("box_max_amp", "箱体最大振幅", "signal", "number", 0.05, 0.60,
                        {"mode": "fixed", "value": BOX_MAX_AMP}, "例如 0.26 表示 26%"),
    ParameterDefinition("breakout_vol_ratio", "突破量 / 箱体均量", "signal", "number", 1.0, 5.0,
                        {"mode": "values", "values": [1.4, 1.6, 1.8]},
                        "突破日成交量相对箱体平均成交量"),
    ParameterDefinition("breakout_chg_min", "突破日最小涨幅", "signal", "number", 0.001, 0.15,
                        {"mode": "fixed", "value": BREAKOUT_CHG_MIN}, "例如 0.02 表示 2%"),
    ParameterDefinition("breakout_chg_max", "突破日最大涨幅", "signal", "number", 0.01, 0.30,
                        {"mode": "fixed", "value": BREAKOUT_CHG_MAX}, "避免把极端单日跳涨混作常规突破"),
    ParameterDefinition("breakout_vs_recent_vol_ratio", "突破量 / 前 5 日均量", "signal", "number", 0.8, 5.0,
                        {"mode": "fixed", "value": BREAKOUT_VS_RECENT_VOL_RATIO}, "放量双重确认"),
    ParameterDefinition("breakout_window_days", "近期突破观察窗", "signal", "integer", 1, 20,
                        {"mode": "fixed", "value": BREAKOUT_WINDOW_DAYS}, "最近多少个交易日内允许出现突破"),
    ParameterDefinition("require_structure", "要求吸筹结构", "signal", "boolean", None, None,
                        {"mode": "fixed", "value": True}, "关闭后属于放宽研究，不等同 A 池"),
    ParameterDefinition("vol_ratio_min", "建仓量 / 前 5 日均量", "exit", "number", 1.0, 4.0,
                        {"mode": "fixed", "value": 1.5}, "用于二次出货基准量识别，不是突破量比"),
    ParameterDefinition("stop_pct", "止损比例", "exit", "number", 0.01, 0.25,
                        {"mode": "values", "values": [0.05, 0.07]}, "触发后按保守规则模拟"),
    ParameterDefinition("exit_window", "二次出货观察窗", "exit", "integer", 3, 40,
                        {"mode": "values", "values": [7, 10, 15]}, "窗口内累计量达到基准后的退出检查"),
    ParameterDefinition("strong_reset", "强势日清零根数", "exit", "integer", 1, 10,
                        {"mode": "fixed", "value": 3}, "连续强势达到该值后重新计量"),
)

_BY_KEY = {item.key: item for item in PARAMETERS}
SIGNAL_KEYS = tuple(item.key for item in PARAMETERS if item.group == "signal")
EXIT_KEYS = tuple(item.key for item in PARAMETERS if item.group == "exit")


class ProfessionalGridError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def parameter_catalog() -> dict[str, Any]:
    return {
        "version": GRID_CONTRACT_VERSION,
        "max_combinations": MAX_COMBINATIONS,
        "parameters": [asdict(item) for item in PARAMETERS],
    }


def normalize_parameters(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        source: dict[str, Any] = {}
    elif isinstance(raw, dict):
        source = raw
    else:
        raise ProfessionalGridError("INVALID_PARAMETER_SPACE", "parameters 必须是对象")
    unknown = sorted(set(source) - set(_BY_KEY))
    if unknown:
        raise ProfessionalGridError(
            "UNKNOWN_PARAMETER", "存在不支持的参数", {"keys": unknown}
        )
    return {
        item.key: _normalize_spec(item, source.get(item.key, item.default))
        for item in PARAMETERS
    }


def expand_parameter_space(
    raw: Any,
    *,
    max_combinations: int = MAX_COMBINATIONS,
) -> dict[str, Any]:
    specs = normalize_parameters(raw)
    values = {key: _expand_spec(_BY_KEY[key], spec) for key, spec in specs.items()}
    raw_count = math.prod(len(v) for v in values.values())
    if raw_count > max_combinations * 4:
        raise ProfessionalGridError(
            "COMBINATION_LIMIT_EXCEEDED",
            f"参数空间理论组合数 {raw_count} 过大",
            {"count": raw_count, "limit": max_combinations},
        )
    signal_combos = _cartesian({key: values[key] for key in SIGNAL_KEYS})
    exit_combos = _cartesian({key: values[key] for key in EXIT_KEYS})
    valid_signal = [combo for combo in signal_combos if _valid_signal_combo(combo)]
    invalid_count = len(signal_combos) - len(valid_signal)
    combos = [
        {"signal": signal, "exit": exit_params}
        for signal in valid_signal
        for exit_params in exit_combos
    ]
    if not combos:
        raise ProfessionalGridError(
            "EMPTY_PARAMETER_SPACE", "参数约束过滤后没有可运行组合"
        )
    if len(combos) > max_combinations:
        raise ProfessionalGridError(
            "COMBINATION_LIMIT_EXCEEDED",
            f"有效组合数 {len(combos)} 超过上限 {max_combinations}",
            {
                "count": len(combos),
                "limit": max_combinations,
                "invalid_signal_combinations": invalid_count,
            },
        )
    canonical = json.dumps(combos, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "specs": specs,
        "values": values,
        "signal_combinations": valid_signal,
        "exit_combinations": exit_combos,
        "combinations": combos,
        "count": len(combos),
        "invalid_signal_combinations": invalid_count,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "horizon": dynamic_horizon(valid_signal),
    }


def dynamic_horizon(signal_combinations: list[dict[str, Any]]) -> int:
    if not signal_combinations:
        return 260
    return max(
        260,
        max(
            int(combo["box_max_days"]) + int(combo["breakout_window_days"]) + 60
            for combo in signal_combinations
        ),
    )


def universe_catalog(db_path: str | Path, *, industry: str | None = None) -> dict[str, Any]:
    rows = _stock_rows(db_path)
    industries: dict[str, int] = {}
    for row in rows:
        label = str(row["industry"] or "未分类")
        industries[label] = industries.get(label, 0) + 1
    selected = rows
    if industry:
        selected = [row for row in rows if str(row["industry"] or "未分类") == industry]
    return {
        "classification_mode": "CURRENT_CLASSIFICATION_FROZEN_UNIVERSE",
        "classification_note": "板块来自当前 stock_basic 快照；运行时冻结股票代码与哈希，不宣称历史行业成员 PIT。",
        "industries": [
            {"name": name, "count": count}
            for name, count in sorted(industries.items(), key=lambda item: (-item[1], item[0]))
        ],
        "stocks": selected[:MAX_UNIVERSE_CODES],
        "stock_count": len(selected),
    }


def resolve_universe(
    db_path: str | Path,
    *,
    industries: Any = None,
    codes: Any = None,
    max_codes: int = 600,
) -> dict[str, Any]:
    limit = max(20, min(int(max_codes), MAX_UNIVERSE_CODES))
    rows = _stock_rows(db_path)
    row_by_code = {str(row["ts_code"]): row for row in rows}
    requested_codes = sorted({str(code).strip().upper() for code in (codes or []) if code})
    requested_industries = sorted({str(value).strip() for value in (industries or []) if value})
    if requested_codes:
        unknown = sorted(set(requested_codes) - set(row_by_code))
        if unknown:
            raise ProfessionalGridError(
                "UNKNOWN_STOCK", "股票池包含未知或非沪深 A 股代码", {"codes": unknown[:20]}
            )
        selected = [row_by_code[code] for code in requested_codes]
        source = "EXPLICIT_STOCKS"
    elif requested_industries:
        known_industries = {str(row["industry"] or "未分类") for row in rows}
        unknown_industries = sorted(set(requested_industries) - known_industries)
        if unknown_industries:
            raise ProfessionalGridError(
                "UNKNOWN_INDUSTRY", "存在未知板块", {"industries": unknown_industries}
            )
        selected = [
            row for row in rows
            if str(row["industry"] or "未分类") in requested_industries
        ]
        source = "CURRENT_INDUSTRIES"
    else:
        selected = rows
        source = "CURRENT_ALL"
    selected = sorted(selected, key=lambda row: str(row["ts_code"]))[:limit]
    if len(selected) < 20:
        raise ProfessionalGridError(
            "UNIVERSE_TOO_SMALL", "专业回测至少需要 20 只股票", {"count": len(selected)}
        )
    frozen_codes = [str(row["ts_code"]) for row in selected]
    canonical = "\n".join(frozen_codes)
    return {
        "source": source,
        "classification_mode": "CURRENT_CLASSIFICATION_FROZEN_UNIVERSE",
        "classification_note": "当前行业分类仅用于选择；回测前冻结代码集合，不能据此声称历史行业分类无未来信息。",
        "industries": requested_industries,
        "codes": frozen_codes,
        "count": len(frozen_codes),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_spec(definition: ParameterDefinition, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"mode": "fixed", "value": raw}
    mode = str(raw.get("mode") or "fixed").lower()
    if mode not in {"fixed", "range", "values"}:
        raise ProfessionalGridError(
            "INVALID_PARAMETER_MODE", f"{definition.title} 的 mode 不支持", {"key": definition.key}
        )
    if definition.value_type == "boolean" and mode == "range":
        raise ProfessionalGridError(
            "INVALID_PARAMETER_MODE", f"{definition.title} 不支持范围步进"
        )
    if mode == "fixed":
        return {"mode": mode, "value": _coerce(definition, raw.get("value"))}
    if mode == "values":
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            raise ProfessionalGridError("INVALID_PARAMETER_VALUES", f"{definition.title} 的 values 不能为空")
        normalized = _dedupe([_coerce(definition, value) for value in values])
        if len(normalized) > 64:
            raise ProfessionalGridError("INVALID_PARAMETER_VALUES", f"{definition.title} 的离散值过多")
        return {"mode": mode, "values": normalized}
    start = _coerce(definition, raw.get("start"))
    stop = _coerce(definition, raw.get("stop"))
    step = _coerce(definition, raw.get("step"), validate_bounds=False)
    if Decimal(str(step)) <= 0 or Decimal(str(start)) > Decimal(str(stop)):
        raise ProfessionalGridError("INVALID_PARAMETER_RANGE", f"{definition.title} 的范围或步长无效")
    return {"mode": mode, "start": start, "stop": stop, "step": step}


def _coerce(
    definition: ParameterDefinition,
    value: Any,
    *,
    validate_bounds: bool = True,
) -> Any:
    if definition.value_type == "boolean":
        if isinstance(value, bool):
            result: Any = value
        elif str(value).lower() in {"true", "false"}:
            result = str(value).lower() == "true"
        else:
            raise ProfessionalGridError("INVALID_PARAMETER_VALUE", f"{definition.title} 必须为布尔值")
    else:
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ProfessionalGridError("INVALID_PARAMETER_VALUE", f"{definition.title} 必须为数字") from None
        if not decimal.is_finite():
            raise ProfessionalGridError("INVALID_PARAMETER_VALUE", f"{definition.title} 必须为有限数字")
        if definition.value_type == "integer":
            if decimal != decimal.to_integral_value():
                raise ProfessionalGridError("INVALID_PARAMETER_VALUE", f"{definition.title} 必须为整数")
            result = int(decimal)
        else:
            result = float(decimal)
    if (
        validate_bounds
        and definition.minimum is not None
        and definition.maximum is not None
        and (float(result) < float(definition.minimum) or float(result) > float(definition.maximum))
    ):
        raise ProfessionalGridError(
            "PARAMETER_OUT_OF_RANGE",
            f"{definition.title} 超出允许范围",
            {"minimum": definition.minimum, "maximum": definition.maximum, "value": result},
        )
    return result


def _expand_spec(definition: ParameterDefinition, spec: dict[str, Any]) -> list[Any]:
    if spec["mode"] == "fixed":
        return [spec["value"]]
    if spec["mode"] == "values":
        return list(spec["values"])
    start = Decimal(str(spec["start"]))
    stop = Decimal(str(spec["stop"]))
    step = Decimal(str(spec["step"]))
    values: list[Any] = []
    current = start
    while current <= stop:
        value: Any = int(current) if definition.value_type == "integer" else float(current)
        values.append(_coerce(definition, value))
        if len(values) > 256:
            raise ProfessionalGridError("INVALID_PARAMETER_RANGE", f"{definition.title} 展开值过多")
        current += step
    return _dedupe(values)


def _cartesian(values: dict[str, list[Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{}]
    for key, choices in values.items():
        result = [{**base, key: value} for base in result for value in choices]
    return result


def _valid_signal_combo(combo: dict[str, Any]) -> bool:
    return (
        int(combo["box_min_days"]) <= int(combo["box_max_days"])
        and float(combo["breakout_chg_min"]) < float(combo["breakout_chg_max"])
    )


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _stock_rows(db_path: str | Path) -> list[dict[str, Any]]:
    path = Path(db_path).resolve()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts_code,name,COALESCE(industry,'未分类') AS industry "
            "FROM stock_basic ORDER BY ts_code"
        ).fetchall()
    return [
        dict(row) for row in rows
        if str(row["ts_code"]).endswith((".SH", ".SZ"))
        and not str(row["ts_code"]).startswith(("4", "8", "9"))
    ]
