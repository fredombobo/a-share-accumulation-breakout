"""公司行为服务：入账/冲正编排 + 日结阻断检查（fail-closed）。

契约（implementation P1.3）：
- `blocking_check(db_path, holdings, as_of)`：持仓标的存在未处理公司行为 →
  返回明细；settlement 在估值/日结前调用，非空即阻断。
- 更正一律走 `apply_reversal`（追加冲正）。
- 表未迁移（v2:corporate_actions 缺失）→ 直接抛错（fail-closed），
  本服务绝不自动执行 DDL（DDL 唯一入口 scripts/migrate_v2.py）。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ab_screener.data.corporate_action_repository import (
    CorporateActionError,
    add_action,
    apply_reversal,
    pending_actions,
)


def _table_ready(db_path: str | Path) -> bool:
    """corporate_actions 账本是否已迁移（未迁移 → 门禁未激活，legacy 路径不变）。"""
    with sqlite3.connect(str(db_path)) as conn:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
        ).fetchone()
    return bool(has)


def _require_tables(db_path: str | Path) -> None:
    """写入路径：表缺失即抛错（fail-closed）；不自动建表。"""
    if not _table_ready(db_path):
        raise CorporateActionError(
            "corporate_actions 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )


def ingest_dividend(
    db_path: str | Path,
    *,
    ts_code: str,
    ex_date: str,
    cash_div_fen: int,
    source: str = "tushare",
) -> int:
    """入账一笔现金分红事件（幂等）。cash_div_fen 单位：分/10股（tushare 口径）。"""
    _require_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        return add_action(
            conn, ts_code=ts_code, ex_date=ex_date, kind="DIVIDEND",
            payload={"cash_div_fen": int(cash_div_fen)}, source=source,
        )


def ingest_split(
    db_path: str | Path,
    *,
    ts_code: str,
    ex_date: str,
    ratio: float,
    source: str = "tushare",
) -> int:
    """入账一笔送转/拆分事件（幂等）。ratio = 新股份数 / 旧股份数。"""
    _require_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        return add_action(
            conn, ts_code=ts_code, ex_date=ex_date, kind="SPLIT",
            payload={"ratio": float(ratio)}, source=source,
        )


def reversal(
    db_path: str | Path,
    *,
    original_id: int,
    payload: dict[str, Any],
    source: str = "manual",
) -> int:
    """追加冲正（更正走追加，不改账本行）。"""
    _require_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        return apply_reversal(conn, original_id=original_id, payload=payload, source=source)


def blocking_check(
    db_path: str | Path,
    holdings: Iterable[str],
    as_of: str,
) -> list[dict[str, Any]]:
    """持仓标的存在 as_of 前未处理公司行为 → 返回明细（非空即阻断日结）。

    账本未迁移（v2:corporate_actions 缺失）→ 门禁未激活，返回空（legacy 路径不变；
    迁移后该阻断自动生效，无需改动 settlement）。
    """
    codes = [c for c in holdings if c]
    if not codes or not _table_ready(db_path):
        return []
    with sqlite3.connect(str(db_path)) as conn:
        return pending_actions(conn, ts_codes=codes, as_of=as_of)


def blocking_summary(db_path: str | Path, holdings: Iterable[str], as_of: str) -> dict[str, Any]:
    blocked = blocking_check(db_path, holdings, as_of)
    return {
        "blocked": bool(blocked),
        "count": len(blocked),
        "actions": blocked,
        "gate_active": _table_ready(db_path),
        "message": (
            "未处理公司行为阻断估值与日结（先处理或冲正后重试）" if blocked else "OK"
        ),
    }
