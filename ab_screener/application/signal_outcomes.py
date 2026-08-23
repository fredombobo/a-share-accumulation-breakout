"""信号 outcome（P4.3）：5/10/20 日理论结果，修订追加版本。

- horizon 表示理论入场后的第 N 个交易所交易日；起点 = 统一执行核心的下一可交易日
  开盘成交价，终点 = 第 N 日 PIT 收盘估值；扣预登记往返成本并计算基准超额。
- 无法在有效期成交 → UNFILLABLE/EXPIRED，收益 NULL（不填 0）。
- 只能在持有期结束、收盘和复权/公司行为数据 available 后回填（调用方保证，本函数校验）。
- 理论 outcome 与纸面 fill 分开统计（独立表/独立查询）。

V2R-S outcome 时点门：ret_5/10/20 只在对应交易日完成且行情
`available_at <= calculation_at` 后回填；否则保持 NULL（UNFILLABLE 不写 0）。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import normalize_ts

_TZ = ZoneInfo("Asia/Shanghai")
HORIZONS = (5, 10, 20)


class OutcomeError(ValueError):
    """outcome 输入非法（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _trade_date(value: str, field: str) -> str:
    """校验并归一化交易日为 YYYYMMDD，拒绝字符串字典序伪比较。"""
    raw = str(value or "")
    try:
        if len(raw) == 8 and raw.isdigit():
            parsed = datetime.strptime(raw, "%Y%m%d").date()
        else:
            parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise OutcomeError(f"{field} 必须为有效交易日: {value!r}") from exc
    return parsed.strftime("%Y%m%d")


def compute_outcome(
    *,
    entry_price_micro: int | None,
    exit_price_micro: int | None,
    cost_rate: float,
    benchmark_excess: float | None = None,
) -> dict[str, Any]:
    """净收益 = (卖出-买入)/买入 - 双边成本；无法成交 → NULL（不填 0）。"""
    if entry_price_micro is None or exit_price_micro is None:
        return {"net_return": None, "benchmark_excess": benchmark_excess}
    if entry_price_micro <= 0:
        raise OutcomeError("入场价必须为正")
    gross = (exit_price_micro - entry_price_micro) / entry_price_micro
    return {
        "net_return": round(gross - 2.0 * cost_rate, 8),
        "benchmark_excess": benchmark_excess,
    }


def record_outcome(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    horizon_days: int,
    status: str,
    entry_price_micro: int | None = None,
    exit_price_micro: int | None = None,
    net_return: float | None = None,
    benchmark_excess: float | None = None,
    available_at: str | None = None,
) -> str:
    """写入 outcome（修订追加：同 observation+horizon 的 revision 递增）。"""
    if horizon_days not in HORIZONS:
        raise OutcomeError(f"horizon_days 必须为 {HORIZONS} 之一: {horizon_days}")
    if status not in ("PENDING", "MATURED", "UNFILLABLE", "EXPIRED"):
        raise OutcomeError(f"非法 outcome 状态: {status}")
    if status in ("MATURED",) and (entry_price_micro is None or exit_price_micro is None):
        raise OutcomeError("MATURED 必须提供入场/出场价")
    if status in ("UNFILLABLE", "EXPIRED") and net_return is not None:
        raise OutcomeError("UNFILLABLE/EXPIRED 收益必须为 NULL（不填 0）")
    available = available_at or _now()
    # 修订版本：同 observation+horizon 追加版本（不覆盖旧结果）
    rev_row = conn.execute(
        "SELECT COALESCE(MAX(revision),0) FROM signal_outcomes"
        " WHERE observation_id=? AND horizon_days=?",
        (observation_id, horizon_days),
    ).fetchone()
    revision = int(rev_row[0] or 0) + 1
    import hashlib

    outcome_id = hashlib.sha256(
        f"{observation_id}|{horizon_days}|{revision}".encode()
    ).hexdigest()[:16]
    conn.execute(
        "INSERT INTO signal_outcomes (outcome_id, observation_id, horizon_days, revision,"
        " status, entry_price_micro, exit_price_micro, net_return, benchmark_excess,"
        " available_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (outcome_id, observation_id, horizon_days, revision, status,
         entry_price_micro, exit_price_micro, net_return, benchmark_excess, available),
    )
    conn.commit()
    return outcome_id


def outcomes_for_observation(
    conn: sqlite3.Connection, observation_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT outcome_id, observation_id, horizon_days, revision, status,"
        " entry_price_micro, exit_price_micro, net_return, benchmark_excess, available_at"
        " FROM signal_outcomes WHERE observation_id=? ORDER BY horizon_days, revision",
        (observation_id,),
    ).fetchall()
    return [
        {
            "outcome_id": r[0], "observation_id": r[1], "horizon_days": r[2],
            "revision": r[3], "status": r[4], "entry_price_micro": r[5],
            "exit_price_micro": r[6], "net_return": r[7],
            "benchmark_excess": r[8], "available_at": r[9],
        }
        for r in rows
    ]


def compute_horizon_result(
    *,
    horizon_days: int,
    entry_price_micro: int | None,
    cost_rate: float,
    exit_price_micro: int | None,
    benchmark_excess: float | None,
    maturity_trade_date: str,
    last_completed_trade_date: str,
    data_available_at: str | None,
    calculation_at: str,
) -> dict[str, Any]:
    """outcome 时点门：交易日完成 + available_at <= calculation_at 才回填。

    - 入场未成交（entry_price_micro 为 None）→ UNFILLABLE，收益 NULL（不填 0）。
    - 对应交易日未完成或行情未 available（越界）→ PENDING，收益 NULL。
    - 条件齐备 → MATURED，返回净收益与基准超额。
    """
    if horizon_days not in HORIZONS:
        raise OutcomeError(f"horizon_days 必须为 {HORIZONS} 之一: {horizon_days}")
    if entry_price_micro is None:
        return {
            "status": "UNFILLABLE", "entry_price_micro": None,
            "exit_price_micro": None, "net_return": None,
            "benchmark_excess": None,
        }
    if entry_price_micro <= 0:
        raise OutcomeError("入场价必须为正")
    maturity_day = _trade_date(maturity_trade_date, "maturity_trade_date")
    completed_day = _trade_date(
        last_completed_trade_date,
        "last_completed_trade_date",
    )
    day_complete = maturity_day <= completed_day
    pit_ok = (
        data_available_at is not None
        and normalize_ts(data_available_at) <= normalize_ts(calculation_at)
    )
    if not (day_complete and pit_ok) or exit_price_micro is None:
        return {
            "status": "PENDING", "entry_price_micro": entry_price_micro,
            "exit_price_micro": None, "net_return": None,
            "benchmark_excess": None,
        }
    computed = compute_outcome(
        entry_price_micro=entry_price_micro,
        exit_price_micro=exit_price_micro,
        cost_rate=cost_rate,
        benchmark_excess=benchmark_excess,
    )
    return {
        "status": "MATURED", "entry_price_micro": entry_price_micro,
        "exit_price_micro": exit_price_micro,
        "net_return": computed["net_return"],
        "benchmark_excess": computed["benchmark_excess"],
    }


def backfill_horizon_outcome(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    horizon_days: int,
    entry_price_micro: int | None,
    cost_rate: float,
    maturity_trade_date: str,
    last_completed_trade_date: str,
    calculation_at: str,
    exit_price_micro: int | None = None,
    data_available_at: str | None = None,
    benchmark_excess: float | None = None,
) -> dict[str, Any]:
    """生产接线：compute_horizon_result → record_outcome（修订追加、重放幂等）。

    同 (observation, horizon) 最新行状态与数值完全一致时跳过写入（不覆盖历史行）。
    """
    result = compute_horizon_result(
        horizon_days=horizon_days,
        entry_price_micro=entry_price_micro,
        cost_rate=cost_rate,
        exit_price_micro=exit_price_micro,
        benchmark_excess=benchmark_excess,
        maturity_trade_date=maturity_trade_date,
        last_completed_trade_date=last_completed_trade_date,
        data_available_at=data_available_at,
        calculation_at=calculation_at,
    )
    rows = [
        r for r in outcomes_for_observation(conn, observation_id)
        if r["horizon_days"] == horizon_days
    ]
    latest = rows[-1] if rows else None
    if latest is not None and (
        latest["status"] == result["status"]
        and latest["entry_price_micro"] == result["entry_price_micro"]
        and latest["exit_price_micro"] == result["exit_price_micro"]
        and latest["net_return"] == result["net_return"]
        and latest["benchmark_excess"] == result["benchmark_excess"]
    ):
        return {"outcome_id": latest["outcome_id"], "idempotent": True, **result}
    outcome_id = record_outcome(
        conn,
        observation_id=observation_id,
        horizon_days=horizon_days,
        status=result["status"],
        entry_price_micro=result["entry_price_micro"],
        exit_price_micro=result["exit_price_micro"],
        net_return=result["net_return"],
        benchmark_excess=result["benchmark_excess"],
        available_at=data_available_at or _now(),
    )
    return {"outcome_id": outcome_id, "idempotent": False, **result}
