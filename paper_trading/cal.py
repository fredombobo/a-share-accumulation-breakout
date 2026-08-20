"""交易日历：Tushare 优先 + 本地推断兜底，落库 trade_cal 表。

- refresh_trade_cal(start, end)：拉 Tushare trade_cal 落库（含 is_open=0）；
  异常/空 → 本地推断（周末剔除 + 内置法定节假日，source='local_infer'）
- is_open(d) / next_open(d) / prev_open(d)：读 trade_cal，无数据按推断兜底
- 对账与可卖日（T+1）用 next_open 计算
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import tx

_TZ = ZoneInfo("Asia/Shanghai")

# 内置法定节假日（2024~2027，仅闭市日；A 股周末必休，无需枚举）
_HOLIDAYS_CN: set[str] = {
    # 2024
    "20240101", "20240209", "20240212", "20240213", "20240214", "20240215", "20240216",
    "20240404", "20240405", "20240501", "20240502", "20240503", "20240610",
    "20240916", "20240917", "20241001", "20241002", "20241003", "20241004", "20241007",
    # 2025
    "20250101", "20250128", "20250129", "20250130", "20250131", "20250203", "20250204",
    "20250404", "20250501", "20250502", "20250505", "20250602",
    "20251001", "20251002", "20251003", "20251006", "20251007", "20251008",
    # 2026（元旦/春节/清明/五一/端午/国庆 估算）
    "20260101", "20260216", "20260217", "20260218", "20260219", "20260220", "20260223",
    "20260406", "20260501", "20260504", "20260505", "20260506", "20260619",
    "20261001", "20261002", "20261005", "20261006", "20261007", "20261008",
    # 2027（估算）
    "20270101", "20270208", "20270209", "20270210", "20270211", "20270212", "20270215",
    "20270405", "20270503", "20270504", "20270505", "20270618",
    "20271001", "20271004", "20271005", "20271006", "20271007", "20271008",
}


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _infer_open(d: date) -> bool:
    """本地推断：非周末且非法定节假日 → 开市。"""
    return not _is_weekend(d) and d.strftime("%Y%m%d") not in _HOLIDAYS_CN


def _iter_dates(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def infer_cal(start: date, end: date) -> list[tuple[str, int, str]]:
    """本地推断日历：[(cal_date, is_open, 'local_infer')]。"""
    return [(d.strftime("%Y%m%d"), 1 if _infer_open(d) else 0, "local_infer")
            for d in _iter_dates(start, end)]


def refresh_trade_cal(
    db_path: str | Path,
    start: str | None = None,
    end: str | None = None,
    store=None,
) -> dict:
    """刷新交易日历落库。Tushare 优先，异常/空 → 本地推断。

    store: 可传 LocalStore（用其在线 pro）；None 时直接 import tushare_init.pro。
    返回 {"source": 'tushare'|'local_infer', "rows": int, "start":..., "end":...}
    """
    from datetime import datetime as _dt

    db_path = Path(db_path)
    today = _dt.now(_TZ).date()
    end_d = date.fromisoformat(end) if end else today
    start_d = date.fromisoformat(start) if start else today - timedelta(days=730)

    rows: list[tuple[str, int, str]] = []
    source = "local_infer"
    try:
        if store is not None:
            pro_client = store._pro
        else:
            from tushare_init import pro as _pro
            pro_client = _pro
        cal = pro_client.trade_cal(
            exchange="", start_date=start_d.strftime("%Y%m%d"),
            end_date=end_d.strftime("%Y%m%d"), fields="cal_date,is_open",
        )
        if cal is not None and not cal.empty:
            for _, r in cal.iterrows():
                rows.append((str(r["cal_date"]), int(r["is_open"]), "tushare"))
            source = "tushare"
    except Exception:  # noqa: BLE001
        rows = []

    if not rows:
        rows = infer_cal(start_d, end_d)
        source = "local_infer"

    now = _dt.now(_TZ).isoformat(timespec="seconds")
    with tx(db_path, immediate=True) as conn:
        for cal_date, is_open, src in rows:
            conn.execute(
                "INSERT INTO trade_cal (cal_date, is_open, source, updated_at)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(cal_date) DO UPDATE SET is_open=excluded.is_open,"
                " source=excluded.source, updated_at=excluded.updated_at",
                (cal_date, is_open, src, now),
            )
    return {"source": source, "rows": len(rows), "start": start_d.isoformat(), "end": end_d.isoformat()}


def _load_cal(db_path: str | Path) -> dict[str, bool]:
    """读取 trade_cal 全部开市状态 → {cal_date: is_open}。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        rows = conn.execute("SELECT cal_date, is_open FROM trade_cal").fetchall()
    return {r[0]: bool(r[1]) for r in rows}


def is_open(db_path: str | Path, d: str) -> bool:
    """指定日期是否开市；trade_cal 无数据按推断兜底。"""
    cal = _load_cal(db_path)
    if d in cal:
        return cal[d]
    return _infer_open(date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}"))


def next_open(db_path: str | Path, d: str) -> str:
    """下一个开市日（含当天，若当天开市）。"""
    cal = _load_cal(db_path)
    cur = date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
    for _ in range(370):
        key = cur.strftime("%Y%m%d")
        open_flag = cal.get(key, _infer_open(cur))
        if open_flag:
            return key
        cur += timedelta(days=1)
    raise RuntimeError(f"无法确定 {d} 之后的交易日")


def prev_open(db_path: str | Path, d: str) -> str:
    """上一个开市日（含当天，若当天开市）。"""
    cal = _load_cal(db_path)
    cur = date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
    for _ in range(370):
        key = cur.strftime("%Y%m%d")
        open_flag = cal.get(key, _infer_open(cur))
        if open_flag:
            return key
        cur -= timedelta(days=1)
    raise RuntimeError(f"无法确定 {d} 之前的交易日")
