"""Tushare → PIT 适配器：仅复用根 tushare_init 初始化路径。

契约（implementation P1.1）：
- 禁止裸 requests / 第二套 Token/URL 初始化；真实调用一律
  `from tushare_init import pro`（唯一标准入口）。
- 适配器把 tushare DataFrame 转成 PIT payload 行（业务键字符串化、其余列保留）。
- 测试可注入 fake `pro`（离线），但生产代码路径不创建任何独立连接。
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES as HISTORY_TABLES
from ab_screener.domain.lhb_contracts import AmountUnit, normalize_top_inst_side

SOURCE = "tushare"
LHB_SOURCE_SCHEMA_VERSION = "v1"
LHB_REQUIRED_FIELDS_V1 = {
    "top_list": frozenset(
        {"ts_code", "trade_date", "reason", "amount", "l_sell", "l_buy", "l_amount", "net_amount"}
    ),
    "top_inst": frozenset(
        {"ts_code", "trade_date", "exalter", "side", "buy", "sell", "net_buy", "reason"}
    ),
    "hm_list": frozenset({"name", "orgs"}),
}
TOP_LIST_REQUIRED_FIELDS = LHB_REQUIRED_FIELDS_V1["top_list"]
TOP_INST_REQUIRED_FIELDS = LHB_REQUIRED_FIELDS_V1["top_inst"]


def _is_missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _finite_non_negative(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数字: {value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} 必须是有限非负数: {value!r}")
    return number


def _clean_non_finite(item: dict[str, Any]) -> None:
    for key, value in tuple(item.items()):
        if isinstance(value, float) and not math.isfinite(value):
            item[key] = None


def validate_top_list_record(item: dict[str, Any]) -> None:
    missing_columns = sorted(TOP_LIST_REQUIRED_FIELDS - set(item))
    if missing_columns:
        raise ValueError(f"适配器载荷缺必需字段 {missing_columns}（top_list）")
    if _is_missing(item.get("ts_code")) or _is_missing(item.get("trade_date")):
        raise ValueError(f"适配器载荷缺业务键列 {item}（top_list）")
    if _is_missing(item.get("reason")) or not str(item.get("reason")).strip():
        raise ValueError("top_list.reason 不能为空")
    for field in ("amount", "l_sell", "l_buy", "l_amount"):
        _finite_non_negative(item.get(field), label=f"top_list.{field}")
    net: Any = item.get("net_amount")
    try:
        net_number = float(net)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"top_list.net_amount 不是有效数字: {net!r}") from exc
    if not math.isfinite(net_number):
        raise ValueError(f"top_list.net_amount 必须是有限数字: {net!r}")


def validate_top_inst_record(item: dict[str, Any]) -> None:
    missing_columns = sorted(TOP_INST_REQUIRED_FIELDS - set(item))
    if missing_columns:
        raise ValueError(f"适配器载荷缺必需字段 {missing_columns}（top_inst）")
    for field in ("ts_code", "trade_date", "exalter", "reason", "side"):
        if _is_missing(item.get(field)) or not str(item.get(field)).strip():
            raise ValueError(f"top_inst.{field} 不能为空")
    side = normalize_top_inst_side(item.get("side"), buy=item.get("buy"), sell=item.get("sell"))
    if side not in {"BUY", "SELL", "BOTH"}:
        raise ValueError(f"top_inst.side 无法识别: {item.get('side')!r}")
    required_amounts = ("buy",) if side == "BUY" else ("sell",) if side == "SELL" else ("buy", "sell")
    for field in required_amounts:
        if _is_missing(item.get(field)):
            raise ValueError(f"top_inst.{field} 在 {side} 行不能为空")
        _finite_non_negative(item.get(field), label=f"top_inst.{field}")
    for field in ({"buy", "sell"} - set(required_amounts)):
        if not _is_missing(item.get(field)):
            _finite_non_negative(item.get(field), label=f"top_inst.{field}")
    net: Any = item.get("net_buy")
    try:
        net_number = float(net)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"top_inst.net_buy 不是有效数字: {net!r}") from exc
    if not math.isfinite(net_number):
        raise ValueError(f"top_inst.net_buy 必须是有限数字: {net!r}")


def pit_history_tables() -> dict[str, list[str]]:
    """PIT 历史表（含 T01 龙虎榜 staging）。不改 aux_history 常量，避免 checksum 漂移。"""
    tables = dict(HISTORY_TABLES)
    from ab_screener.data.migration_intents.lhb_tracking_v2 import LHB_PIT_HISTORY_TABLES

    tables.update(LHB_PIT_HISTORY_TABLES)
    return tables


def infer_top_inst_side(buy: Any, sell: Any) -> str:
    return normalize_top_inst_side(None, buy=buy, sell=sell)


def prepare_top_list_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验 top_list 完整金额口径，并清理可选字段中的非有限值。"""
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        validate_top_list_record(item)
        item["ts_code"] = str(item["ts_code"]).strip()
        item["trade_date"] = str(item["trade_date"]).strip()
        item["reason"] = str(item["reason"]).strip()
        item["amount_unit"] = AmountUnit.YUAN.value
        _clean_non_finite(item)
        out.append(item)
    return out


def prepare_top_inst_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐 top_inst 业务键：reason / side。Tushare side '0'/'1' 映射为 BUY/SELL。"""
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        validate_top_inst_record(item)
        item["ts_code"] = str(item.get("ts_code") or "").strip()
        item["trade_date"] = str(item.get("trade_date") or "").strip()
        item["exalter"] = str(item.get("exalter") or "").strip()
        reason = item.get("reason")
        item["reason"] = str(reason).strip() if reason not in (None, "") else "UNKNOWN"
        item["side"] = normalize_top_inst_side(
            item.get("side"), buy=item.get("buy"), sell=item.get("sell")
        )
        item["amount_unit"] = AmountUnit.YUAN.value
        _clean_non_finite(item)
        if not item["ts_code"] or not item["trade_date"] or not item["exalter"]:
            raise ValueError(f"适配器载荷缺业务键列 {item}（top_inst）")
        out.append(item)
    return out


def prepare_hm_list_records(rows: list[dict[str, Any]], *, list_date: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        if "orgs" not in item:
            raise ValueError("适配器载荷缺必需字段 ['orgs']（hm_list）")
        orgs = item.get("orgs")
        try:
            parsed_orgs = json.loads(orgs) if isinstance(orgs, str) else orgs
        except json.JSONDecodeError as exc:
            raise ValueError("hm_list.orgs 不是合法 JSON 数组") from exc
        if not isinstance(parsed_orgs, list) or any(not isinstance(v, str) for v in parsed_orgs):
            raise ValueError("hm_list.orgs 必须是字符串数组")
        name = item.get("hm_name") or item.get("name")
        if _is_missing(name):
            raise ValueError("hm_list.name 不能为空")
        item["hm_name"] = str(name or "").strip()
        item["list_date"] = str(list_date)
        if not item["hm_name"] or not item["list_date"]:
            raise ValueError(f"适配器载荷缺业务键列 {item}（hm_list）")
        item["orgs"] = json.dumps(parsed_orgs, ensure_ascii=False, separators=(",", ":"))
        out.append(item)
    return out


def get_pro_handle(pro: Any | None = None) -> Any:
    """返回 pro 句柄：显式注入优先（离线测试），否则走根 tushare_init。"""
    if pro is not None:
        return pro
    from tushare_init import pro as root_pro  # 唯一标准初始化路径

    return root_pro


def df_to_pit_rows(df: pd.DataFrame, dataset: str) -> list[dict[str, Any]]:
    """DataFrame → PIT 载荷行：业务键字符串化；缺键列 → 拒绝。"""
    tables = pit_history_tables()
    table = f"{dataset}_history"
    if table not in tables:
        raise ValueError(f"未知 PIT 数据集: {dataset}")
    key_cols = tables[table]
    if df is None or df.empty:
        return []
    missing = [c for c in key_cols if c not in df.columns]
    if missing:
        raise ValueError(f"适配器载荷缺业务键列 {missing}（{dataset}）")
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        if dataset == "top_list":
            record = prepare_top_list_records([record])[0]
        elif dataset == "top_inst":
            validate_top_inst_record(record)
        row: dict[str, Any] = {}
        for c, v in record.items():
            if c in key_cols:
                row[c] = str(v) if v is not None and str(v) not in ("", "nan") else None
            else:
                row[c] = v
        if table == "top_inst_history":
            row["side"] = normalize_top_inst_side(
                record.get("side"), buy=record.get("buy"), sell=record.get("sell")
            )
            row["amount_unit"] = AmountUnit.YUAN.value
            _clean_non_finite(row)
        elif dataset == "top_list":
            row["amount_unit"] = AmountUnit.YUAN.value
            _clean_non_finite(row)
        if any(row.get(c) in (None, "") for c in key_cols):
            raise ValueError(f"适配器行业务键缺失: {row}（{dataset}）")
        rows.append(row)
    return rows


def fetch_pit_rows(
    dataset: str,
    *,
    start: str,
    end: str,
    pro: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按数据集拉取并转换为 PIT 载荷行（真实网络路径，仅根 tushare_init）。"""
    handle = get_pro_handle(pro)
    kwargs = {"start_date": start, "end_date": end, **(extra or {})}
    if dataset == "daily":
        df = handle.daily(**kwargs)
    elif dataset == "daily_basic":
        df = handle.daily_basic(**kwargs)
    elif dataset == "moneyflow":
        df = handle.moneyflow(**kwargs)
    elif dataset == "adj_factor":
        df = handle.adj_factor(**kwargs)
    elif dataset == "fina_indicator":
        df = handle.fina_indicator(period=end, **(extra or {}))
    elif dataset == "stock_basic":
        df = handle.stock_basic(**(extra or {}))
    elif dataset == "top_list":
        df = handle.top_list(trade_date=end, **(extra or {}))
    elif dataset == "top_inst":
        df = handle.top_inst(trade_date=end, **(extra or {}))
        if df is None or getattr(df, "empty", False):
            return []
        return df_to_pit_rows(pd.DataFrame(prepare_top_inst_records(df.to_dict("records"))), dataset)
    elif dataset == "hm_list":
        df = handle.hm_list(**(extra or {}))
        if df is None or getattr(df, "empty", False):
            return []
        return df_to_pit_rows(
            pd.DataFrame(prepare_hm_list_records(df.to_dict("records"), list_date=end)), dataset
        )
    elif dataset == "margin":
        df = handle.margin_detail(trade_date=end, **(extra or {}))
    elif dataset == "cyq":
        df = handle.cyq_perf(trade_date=end, **(extra or {}))
    elif dataset == "holder":
        df = handle.top10_holders(ts_code=end, **(extra or {}))  # 按 ts_code 拉取
    else:
        raise ValueError(f"适配器不支持数据集: {dataset}")
    return df_to_pit_rows(df, dataset)
