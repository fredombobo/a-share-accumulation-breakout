"""
本地持仓跟踪（JSON）
====================
路径：runtime/portfolio.json
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import _BASE_DIR

PORTFOLIO_PATH = Path(_BASE_DIR) / "runtime" / "portfolio.json"


def _default() -> dict[str, Any]:
    return {"updated_at": None, "positions": []}


def load_portfolio(path: Path | None = None) -> dict[str, Any]:
    p = path or PORTFOLIO_PATH
    if not p.exists():
        return _default()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if "positions" not in data:
            data["positions"] = []
        return data
    except Exception:  # noqa: BLE001
        return _default()


def save_portfolio(data: dict[str, Any], path: Path | None = None) -> Path:
    p = path or PORTFOLIO_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def upsert_position(
    ts_code: str,
    *,
    name: str = "",
    cost: float | None = None,
    shares: float | None = None,
    stop_loss: float | None = None,
    note: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    data = load_portfolio(path)
    code = ts_code.upper()
    found = False
    for pos in data["positions"]:
        if str(pos.get("ts_code", "")).upper() == code:
            if name:
                pos["name"] = name
            if cost is not None:
                pos["cost"] = cost
            if shares is not None:
                pos["shares"] = shares
            if stop_loss is not None:
                pos["stop_loss"] = stop_loss
            if note:
                pos["note"] = note
            pos["updated_at"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if not found:
        data["positions"].append({
            "ts_code": code,
            "name": name,
            "cost": cost,
            "shares": shares,
            "stop_loss": stop_loss,
            "note": note,
            "opened_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    save_portfolio(data, path)
    return data


def remove_position(ts_code: str, path: Path | None = None) -> dict[str, Any]:
    data = load_portfolio(path)
    code = ts_code.upper()
    data["positions"] = [p for p in data["positions"] if str(p.get("ts_code", "")).upper() != code]
    save_portfolio(data, path)
    return data


def check_stops(price_map: dict[str, float], path: Path | None = None) -> list[dict[str, Any]]:
    """对照最新价检查是否触发止损。price_map: ts_code -> last_close"""
    data = load_portfolio(path)
    alerts = []
    for pos in data["positions"]:
        code = str(pos.get("ts_code", "")).upper()
        px = price_map.get(code)
        stop = pos.get("stop_loss")
        if px is None or stop is None:
            continue
        try:
            px_f, stop_f = float(px), float(stop)
        except (TypeError, ValueError):
            continue
        if px_f <= stop_f:
            alerts.append({
                "ts_code": code,
                "name": pos.get("name"),
                "price": px_f,
                "stop_loss": stop_f,
                "status": "STOP_HIT",
                "msg": f"现价 {px_f} ≤ 止损 {stop_f}",
            })
        else:
            alerts.append({
                "ts_code": code,
                "name": pos.get("name"),
                "price": px_f,
                "stop_loss": stop_f,
                "status": "OK",
                "msg": f"距止损 {(px_f - stop_f) / px_f * 100:.1f}%",
            })
    return alerts
