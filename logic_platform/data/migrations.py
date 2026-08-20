"""logic_platform SQLite 迁移。

风格对齐 paper_trading/migrations.py：
  - 共用 schema_version 表；本包独立版本段 101+，与 paper 1–8 互不冲突
  - 事务复用 paper_trading/db.py:tx（BEGIN IMMEDIATE）
  - 幂等：重复执行 no-op

表（docs/VOLUME-PRICE-LOGIC-PLATFORM.md §7.1）：
  features_daily / structure_state_daily / logic_strategies /
  logic_backtests / logic_predictions
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_trading.db import tx

_LOGGER = logging.getLogger(__name__)
_TZ = ZoneInfo("Asia/Shanghai")

MigrationFn = Callable[[sqlite3.Connection], None]

_DDL: dict[str, str] = {
    "features_daily": """
        CREATE TABLE IF NOT EXISTS features_daily (
          ts_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          feature_version TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          PRIMARY KEY (ts_code, trade_date, feature_version)
        )
    """,
    "structure_state_daily": """
        CREATE TABLE IF NOT EXISTS structure_state_daily (
          ts_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          state TEXT NOT NULL,
          box_high REAL,
          box_low REAL,
          box_mid REAL,
          breakout_date TEXT,
          reasons_json TEXT,
          PRIMARY KEY (ts_code, trade_date)
        )
    """,
    "logic_strategies": """
        CREATE TABLE IF NOT EXISTS logic_strategies (
          id TEXT PRIMARY KEY,
          version TEXT NOT NULL,
          name TEXT NOT NULL,
          dsl_yaml TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft',
          research_only INTEGER NOT NULL DEFAULT 1,
          metrics_json TEXT,
          created_at TEXT,
          updated_at TEXT
        )
    """,
    "logic_backtests": """
        CREATE TABLE IF NOT EXISTS logic_backtests (
          run_id TEXT PRIMARY KEY,
          strategy_id TEXT NOT NULL,
          params_json TEXT,
          window_json TEXT,
          metrics_json TEXT,
          equity_path TEXT,
          created_at TEXT
        )
    """,
    "logic_predictions": """
        CREATE TABLE IF NOT EXISTS logic_predictions (
          ts_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          model_version TEXT NOT NULL,
          horizon INTEGER NOT NULL,
          p_up REAL,
          expected_ret REAL,
          fail_risk REAL,
          PRIMARY KEY (ts_code, trade_date, model_version, horizon)
        )
    """,
}


def mig_logic_tables_v101(conn: sqlite3.Connection) -> None:
    """v101：建 logic_platform 五张基础表。"""
    for ddl in _DDL.values():
        conn.execute(ddl)
    # 状态约束索引（查询常用列）
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_state_daily_code_date "
        "ON structure_state_daily (ts_code, trade_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_features_code_date "
        "ON features_daily (ts_code, trade_date)"
    )


MIGRATIONS: list[tuple[int, str, MigrationFn]] = [
    (101, "M101_logic_tables", mig_logic_tables_v101),
]


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def run_logic_migrations(db_path: str | Path, verbose: bool = False) -> list[int]:
    """执行缺失的 logic 迁移，返回本次应用的版本列表（空 = 无迁移）。"""
    db_path = Path(db_path)
    # 确保 schema_version 表存在（与 paper 共用，幂等）
    with tx(db_path, immediate=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
              version   INTEGER PRIMARY KEY,
              name      TEXT NOT NULL,
              checksum  TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
        """)
        applied = {r[0] for r in conn.execute(
            "SELECT version FROM schema_version").fetchall()}

    applied_now: list[int] = []
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        src = inspect.getsource(fn)
        checksum = hashlib.sha1(src.encode("utf-8")).hexdigest()
        with tx(db_path, immediate=True) as conn:
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, name, checksum, applied_at) "
                "VALUES (?,?,?,?)",
                (version, name, checksum, _now_iso()),
            )
        applied_now.append(version)
        if verbose:
            print(f"[logic-migrate] 应用 {name} (v{version})")
    return applied_now


def schema_version(db_path: str | Path) -> int | None:
    """当前 schema_version 最大值（共表读取，缺表返回 None）。"""
    try:
        with tx(db_path, immediate=False) as conn:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("schema_version 读取失败: %s", exc)
        return None
