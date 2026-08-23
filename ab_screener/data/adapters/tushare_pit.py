"""Tushare → PIT 适配器：仅复用根 tushare_init 初始化路径。

契约（implementation P1.1 / V2R-D）：
- 禁止裸 requests / 第二套 Token/URL 初始化；真实调用一律
  `from tushare_init import pro`（唯一标准入口）。
- 适配器把 tushare DataFrame 转成 PIT payload 行（业务键字符串化、其余列保留）。
- 公司行为拉取（dividend）无权限/接口异常时显式抛 CorporateActionError
  （fail-closed，不得静默返回空表伪装成「无公司行为」）。
- 测试可注入 fake `pro`（离线），但生产代码路径不创建任何独立连接。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.data.corporate_action_repository import CorporateActionError
from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES as HISTORY_TABLES

SOURCE = "tushare"


def get_pro_handle(pro: Any | None = None) -> Any:
    """返回 pro 句柄：显式注入优先（离线测试），否则走根 tushare_init。"""
    if pro is not None:
        return pro
    from tushare_init import pro as root_pro  # 唯一标准初始化路径

    return root_pro


def df_to_pit_rows(df: pd.DataFrame, dataset: str) -> list[dict[str, Any]]:
    """DataFrame → PIT 载荷行：业务键字符串化；缺键列 → 拒绝。"""
    table = f"{dataset}_history"
    if table not in HISTORY_TABLES:
        raise ValueError(f"未知 PIT 数据集: {dataset}")
    key_cols = HISTORY_TABLES[table]
    if df is None or df.empty:
        return []
    missing = [c for c in key_cols if c not in df.columns]
    if missing:
        raise ValueError(f"适配器载荷缺业务键列 {missing}（{dataset}）")
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        row: dict[str, Any] = {}
        for c, v in record.items():
            if c in key_cols:
                row[c] = str(v) if v is not None and str(v) not in ("", "nan") else None
            else:
                row[c] = v
        if any(row.get(c) in (None, "") for c in key_cols):
            raise ValueError(f"适配器行业务键缺失: {row}（{dataset}）")
        rows.append(row)
    return rows


def fetch_corporate_actions(
    pro: Any | None = None,
    *,
    ts_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    fields: str | None = None,
) -> list[dict[str, Any]]:
    """拉取公司行为（dividend）并转成账本 payload 行。

    - 无权限/接口异常 → 显式抛 CorporateActionError（fail-closed）。
    - 返回行结构：ts_code / ex_date / kind / payload / source。
      kind 由 payload 推导：有送转 → SPLIT，否则 DIVIDEND。
    - effective_at 与 available_at 由调用方（backfill）在入库时赋值。
    """
    handle = get_pro_handle(pro)
    kwargs: dict[str, Any] = {}
    if ts_code:
        kwargs["ts_code"] = ts_code
    if start_date:
        kwargs["ann_date"] = start_date
    if end_date:
        kwargs["ann_date"] = end_date
    try:
        df = handle.dividend(
            fields=fields
            or "ts_code,ann_date,div_proc,stk_div,cash_div_tax,record_date,ex_date,pay_date",
            **kwargs,
        )
    except Exception as exc:
        raise CorporateActionError(f"公司行为接口不可用或无权限: {exc}") from exc
    if df is None:
        raise CorporateActionError("公司行为接口返回空（无权限或接口异常）")
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        ts = record.get("ts_code")
        ex_date = record.get("ex_date")
        if not ts or not ex_date:
            continue
        stk_div = record.get("stk_div")
        try:
            has_stock = stk_div is not None and float(stk_div) > 0
        except (TypeError, ValueError):
            has_stock = False
        payload = {
            "ann_date": _as_str(record.get("ann_date")),
            "div_proc": _as_str(record.get("div_proc")),
            "stk_div": _as_float(record.get("stk_div")),
            "cash_div_tax": _as_float(record.get("cash_div_tax")),
            "record_date": _as_str(record.get("record_date")),
            "ex_date": _as_str(ex_date),
            "pay_date": _as_str(record.get("pay_date")),
        }
        rows.append(
            {
                "ts_code": str(ts),
                "ex_date": _as_str(ex_date),
                "kind": "SPLIT" if has_stock else "DIVIDEND",
                "payload": {k: v for k, v in payload.items() if v is not None},
                "source": SOURCE,
            }
        )
    return rows


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "nan", "None"):
        return None
    return text


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    elif dataset == "margin":
        df = handle.margin_detail(trade_date=end, **(extra or {}))
    elif dataset == "cyq":
        df = handle.cyq_perf(trade_date=end, **(extra or {}))
    elif dataset == "holder":
        df = handle.top10_holders(ts_code=end, **(extra or {}))  # 按 ts_code 拉取
    else:
        raise ValueError(f"适配器不支持数据集: {dataset}")
    return df_to_pit_rows(df, dataset)
