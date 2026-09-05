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
from ab_screener.domain.market_classification import (
    CLASSIFICATIONS,
    DEFAULT_CLASSIFICATION,
    ClassificationDefinition,
    get_classification,
    normalize_group,
)
from config import (
    BENCH_MAX_HOLD_DAYS,
    BOX_MAX_AMP,
    BREAKOUT_CHG_MAX,
    BREAKOUT_CHG_MIN,
    BREAKOUT_VS_RECENT_VOL_RATIO,
)

LONG_RUNNING_WARNING_COMBINATIONS = 512
MAX_COMBINATIONS = 5_120
MAX_UNIVERSE_CODES = 1500
GRID_CONTRACT_VERSION = "professional-grid-v1.3.0"


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
    ParameterDefinition("target_pct", "止盈比例", "exit", "number", 0.02, 1.00,
                        {"mode": "values", "values": [0.10, 0.12, 0.15]},
                        "买入后下一交易日起触发；同日同时触及止损时优先按止损"),
    ParameterDefinition("max_hold_days", "最长持有天数", "exit", "integer", 2, 120,
                        {"mode": "fixed", "value": BENCH_MAX_HOLD_DAYS},
                        "从信号次日开盘买入后计算；到期按收盘价退出"),
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
        "long_running_warning_combinations": LONG_RUNNING_WARNING_COMBINATIONS,
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
        "long_running": len(combos) > LONG_RUNNING_WARNING_COMBINATIONS,
        "long_running_warning_combinations": LONG_RUNNING_WARNING_COMBINATIONS,
        "invalid_signal_combinations": invalid_count,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "horizon": dynamic_horizon(valid_signal),
    }


def validate_fixed_parameters(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate one complete manual profile against the professional contract."""
    if not isinstance(raw, dict):
        raise ProfessionalGridError("INVALID_MANUAL_PARAMETERS", "手工参数必须是对象")
    source = dict(raw)
    # v1.2 clients did not expose the true max-hold parameter.  Preserve their
    # exact 30-day engine behavior instead of rejecting an otherwise complete
    # manual profile during the v1.3 transition.
    source.setdefault("max_hold_days", BENCH_MAX_HOLD_DAYS)
    missing = sorted(set(_BY_KEY) - set(source))
    unknown = sorted(set(source) - set(_BY_KEY))
    if missing:
        raise ProfessionalGridError(
            "MISSING_MANUAL_PARAMETER",
            "手工参数不完整",
            {"keys": missing},
        )
    if unknown:
        raise ProfessionalGridError(
            "UNKNOWN_PARAMETER",
            "存在不支持的参数",
            {"keys": unknown},
        )
    expanded = expand_parameter_space(
        {key: {"mode": "fixed", "value": source[key]} for key in _BY_KEY},
        max_combinations=1,
    )
    combination = expanded["combinations"][0]
    return {
        "signal": dict(combination["signal"]),
        "exit": dict(combination["exit"]),
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


def _professional_classification(value: str | None) -> ClassificationDefinition:
    try:
        return get_classification(value)
    except ValueError as exc:
        raise ProfessionalGridError(
            "UNKNOWN_CLASSIFICATION",
            str(exc),
            {"classification": str(value or "")},
        ) from exc


def universe_catalog(
    db_path: str | Path,
    *,
    classification: str = DEFAULT_CLASSIFICATION,
    group: str | None = None,
    industry: str | None = None,
) -> dict[str, Any]:
    rows = _stock_rows(db_path)
    if industry and not group:
        classification = "industry"
        group = industry
    definition = _professional_classification(classification)
    group_counts: dict[str, int] = {}
    for row in rows:
        label = normalize_group(row.get(definition.column))
        group_counts[label] = group_counts.get(label, 0) + 1
    selected = rows
    if group:
        if group not in group_counts:
            raise ProfessionalGridError(
                "UNKNOWN_CLASSIFICATION_GROUP",
                f"{definition.title}中不存在分组 {group}",
                {"classification": definition.key, "groups": [group]},
            )
        selected = [
            row for row in rows if normalize_group(row.get(definition.column)) == group
        ]
    dimensions: list[dict[str, Any]] = []
    for item in CLASSIFICATIONS:
        values = {normalize_group(row.get(item.column)) for row in rows}
        dimensions.append({**item.public(), "group_count": len(values)})
    groups = [
        {"name": name, "count": count}
        for name, count in sorted(group_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "classification": definition.key,
        "classification_title": definition.title,
        "group_label": definition.group_label,
        "classification_mode": "CURRENT_CLASSIFICATION_FROZEN_UNIVERSE",
        "classification_note": (
            f"{definition.title}来自当前 stock_basic 快照；运行时冻结股票代码与哈希，"
            "不宣称历史分类成员 PIT。"
        ),
        "classifications": dimensions,
        "groups": groups,
        # 旧客户端兼容：默认行业请求继续读取 industries。
        "industries": groups if definition.key == "industry" else [],
        "stocks": selected[:MAX_UNIVERSE_CODES],
        "stock_count": len(selected),
    }


def resolve_universe(
    db_path: str | Path,
    *,
    classification: str = DEFAULT_CLASSIFICATION,
    groups: Any = None,
    industries: Any = None,
    codes: Any = None,
    max_codes: int = 600,
) -> dict[str, Any]:
    limit = max(20, min(int(max_codes), MAX_UNIVERSE_CODES))
    rows = _stock_rows(db_path)
    definition = _professional_classification(classification)
    row_by_code = {str(row["ts_code"]): row for row in rows}
    requested_codes = sorted({str(code).strip().upper() for code in (codes or []) if code})
    raw_groups = groups
    if raw_groups is None and industries is not None:
        if definition.key != "industry":
            raise ProfessionalGridError(
                "INVALID_UNIVERSE",
                "industries 兼容字段只能用于细分行业分类",
                {"classification": definition.key},
            )
        raw_groups = industries
    requested_groups = sorted({str(value).strip() for value in (raw_groups or []) if value})
    if requested_codes:
        unknown = sorted(set(requested_codes) - set(row_by_code))
        if unknown:
            raise ProfessionalGridError(
                "UNKNOWN_STOCK", "股票池包含未知或非沪深 A 股代码", {"codes": unknown[:20]}
            )
        selected = [row_by_code[code] for code in requested_codes]
        source = "EXPLICIT_STOCKS"
    elif requested_groups:
        known_groups = {normalize_group(row.get(definition.column)) for row in rows}
        unknown_groups = sorted(set(requested_groups) - known_groups)
        if unknown_groups:
            raise ProfessionalGridError(
                "UNKNOWN_CLASSIFICATION_GROUP",
                f"{definition.title}中存在未知分组",
                {"classification": definition.key, "groups": unknown_groups},
            )
        selected = [
            row for row in rows
            if normalize_group(row.get(definition.column)) in requested_groups
        ]
        source = "CURRENT_CLASSIFICATION_GROUPS"
    else:
        selected = rows
        source = "CURRENT_ALL"
    population = selected
    if requested_codes and len(selected) > limit:
        raise ProfessionalGridError(
            "EXPLICIT_UNIVERSE_EXCEEDS_LIMIT",
            "勾选股票数超过样本上限，请提高上限或减少勾选；不会静默截断",
            {"selected": len(selected), "limit": limit},
        )
    selected = _stratified_sample(selected, limit)
    if len(selected) < 20:
        raise ProfessionalGridError(
            "UNIVERSE_TOO_SMALL", "专业回测至少需要 20 只股票", {"count": len(selected)}
        )
    frozen_codes = [str(row["ts_code"]) for row in selected]
    canonical = "\n".join(frozen_codes)
    return {
        "source": source,
        "classification": definition.key,
        "classification_title": definition.title,
        "group_label": definition.group_label,
        "classification_mode": "CURRENT_CLASSIFICATION_FROZEN_UNIVERSE",
        "classification_note": (
            f"当前{definition.title}仅用于选择；回测前冻结代码集合，"
            "不能据此声称历史分类无未来信息。"
        ),
        "groups": requested_groups,
        "industries": requested_groups if definition.key == "industry" else [],
        "codes": frozen_codes,
        "count": len(frozen_codes),
        "industry_by_code": {str(row["ts_code"]): normalize_group(row.get("industry")) for row in selected},
        "sampling": {
            "version": "exchange-industry-hamilton-sha256-v1",
            "seed": "ab-personal-research-20260905",
            "method": "EXPLICIT_ALL" if requested_codes else "STRATIFIED",
            "population_count": len(population),
            "population_exchanges": _distribution(population, "exchange"),
            "sample_exchanges": _distribution(selected, "exchange"),
            "sample_industries": _distribution(selected, "industry"),
        },
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _distribution(rows: list[dict[str, Any]], dimension: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = (str(row["ts_code"]).split(".")[-1] if dimension == "exchange"
               else normalize_group(row.get(dimension)))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _stratified_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Proportional largest-remainder quotas, then stable seeded hash selection.

    Stratify by exchange first and industry second; never take the code-sorted
    prefix. This is a reproducible *current* universe, not a PIT membership claim.
    """
    def take(items: list[dict[str, Any]], count: int, level: int) -> list[dict[str, Any]]:
        if count >= len(items):
            return items
        if not count:
            return []
        if level == 2:
            return sorted(items, key=lambda row: hashlib.sha256(
                f"ab-personal-research-20260905:{row['ts_code']}".encode()
            ).hexdigest())[:count]
        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            key = (str(item["ts_code"]).split(".")[-1] if level == 0
                   else normalize_group(item.get("industry")))
            buckets.setdefault(key, []).append(item)
        quotas = {key: count * len(values) // len(items) for key, values in buckets.items()}
        remainder_order = sorted(buckets, key=lambda key: (
            -(count * len(buckets[key]) % len(items)), key,
        ))
        for key in remainder_order[:count - sum(quotas.values())]:
            quotas[key] += 1
        return [row for key in sorted(buckets) for row in take(buckets[key], quotas[key], level + 1)]

    return sorted(take(rows, limit, 0), key=lambda row: str(row["ts_code"]))


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
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(stock_basic)").fetchall()
        }
        dimension_sql = [
            f"COALESCE({item.column},'未分类') AS {item.column}"
            if item.column in columns
            else f"'未分类' AS {item.column}"
            for item in CLASSIFICATIONS
        ]
        rows = conn.execute(
            f"SELECT ts_code,name,{','.join(dimension_sql)} "
            "FROM stock_basic ORDER BY ts_code"
        ).fetchall()
    return [
        dict(row) for row in rows
        if str(row["ts_code"]).endswith((".SH", ".SZ"))
        and not str(row["ts_code"]).startswith(("4", "8", "9"))
    ]
