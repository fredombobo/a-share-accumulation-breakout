"""涨停/跌停梯队（只读，口径对齐 astock 板宽规则）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def board_limit_pct(ts_code: str) -> float:
    """主板约 10%；创业/科创 20%。返回百分比上限。"""
    code = (ts_code or "").split(".")[0]
    if code.startswith(("300", "301", "688")):
        return 20.0
    return 10.0


def _pct(close: float, pre_close: float) -> float | None:
    if pre_close <= 0:
        return None
    return (close / pre_close - 1.0) * 100.0


def limit_up_ladder(
    db_path: str | Path,
    trade_date: str,
    *,
    top_n: int = 20,
) -> dict[str, Any]:
    """按 close/pre_close 识别涨停/跌停。无行 → INSUFFICIENT。"""
    path = Path(db_path)
    if not path.is_file():
        return {
            "trade_date": trade_date,
            "status": "INSUFFICIENT",
            "reason": "db_missing",
            "limit_up": 0,
            "limit_down": 0,
            "items": [],
        }
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT ts_code, close, pre_close FROM daily"
            " WHERE trade_date=? AND pre_close>0 AND close>0",
            (trade_date,),
        ).fetchall()
    if not rows:
        return {
            "trade_date": trade_date,
            "status": "INSUFFICIENT",
            "reason": "no_bars",
            "limit_up": 0,
            "limit_down": 0,
            "items": [],
        }
    up_items: list[dict[str, Any]] = []
    down_n = 0
    for ts_code, close, pre_close in rows:
        pct = _pct(float(close), float(pre_close))
        if pct is None:
            continue
        lim = board_limit_pct(str(ts_code))
        # 与 astock 一致：距涨停不足 0.1pct 视为涨停
        if pct >= lim - 0.1:
            up_items.append({
                "ts_code": str(ts_code),
                "pct_chg": round(pct, 2),
                "board_limit_pct": lim,
            })
        elif pct <= -(lim - 0.1):
            down_n += 1
    up_items.sort(key=lambda x: x["pct_chg"], reverse=True)
    return {
        "trade_date": trade_date,
        "status": "PASS",
        "reason": None,
        "limit_up": len(up_items),
        "limit_down": down_n,
        "items": up_items[: max(0, min(int(top_n), 20))],
    }
