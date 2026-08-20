"""信号 outcome（P4.3）：5/10/20 日理论结果，修订追加版本。

- horizon 表示理论入场后的第 N 个交易所交易日；起点 = 统一执行核心的下一可交易日
  开盘成交价，终点 = 第 N 日 PIT 收盘估值；扣预登记往返成本并计算基准超额。
- 无法在有效期成交 → UNFILLABLE/EXPIRED，收益 NULL（不填 0）。
- 只能在持有期结束、收盘和复权/公司行为数据 available 后回填（调用方保证，本函数校验）。
- 理论 outcome 与纸面 fill 分开统计（独立表/独立查询）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
HORIZONS = (5, 10, 20)


class OutcomeError(ValueError):
    """outcome 输入非法（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


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
