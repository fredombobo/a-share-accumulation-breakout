"""增量迁移机制：schema_version 版本表 + 有序迁移列表。

特性：
  - 空库：无 schema_version → 全部迁移按序执行
  - 已有库（含 938MB 现网库）：只执行缺失版本
  - 重复执行：版本全在 → no-op（幂等）
  - 每个迁移独立 BEGIN IMMEDIATE 事务；迁移函数内部再叠 PRAGMA 检列，双重幂等
  - 只新增表/列，绝不 DROP / 修改原表结构与数据
"""
from __future__ import annotations

import hashlib
import inspect
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .db import tx
from .schema import PAPER_TABLE_NAMES, paper_ddl_statements

_TZ = ZoneInfo("Asia/Shanghai")

MigrationFn = Callable[[sqlite3.Connection], None]


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    """幂等加列：PRAGMA 检列，缺失才 ALTER TABLE ADD COLUMN。"""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col, col_type in cols.items():
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


# ── M001：daily 行情元数据列 + 存量标记 ──

def _next_open_date(conn: sqlite3.Connection, d: str) -> str:
    """下一个开市日：trade_cal 优先（若已建表且有数据），否则周末推断兜底。

    旧行情「可用时点」保守设置为下一交易日开盘前 09:30。
    """
    cal_dates = None
    try:
        has_cal = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_cal'"
        ).fetchone()
        if has_cal:
            rows = conn.execute(
                "SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date > ? ORDER BY cal_date LIMIT 1",
                (d,),
            ).fetchall()
            if rows:
                cal_dates = rows[0][0]
    except Exception:  # noqa: BLE001
        cal_dates = None
    if cal_dates:
        return cal_dates
    # 周末推断兜底：从 d+1 起找第一个工作日
    cur = datetime.strptime(d, "%Y%m%d").date() + timedelta(days=1)
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur.strftime("%Y%m%d")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def mig_daily_metadata(conn: sqlite3.Connection) -> None:
    """M001：daily 加 5 个元数据列，存量行标记 legacy_backfill（按交易日分批避免长锁）。

    daily 表由 LocalStore._init_schema 创建；直接对空库跑迁移器时表不存在 → 跳过。
    """
    if not _table_exists(conn, "daily"):
        return
    _ensure_columns(conn, "daily", {
        "available_at": "TEXT",
        "ingested_at": "TEXT",
        "source": "TEXT NOT NULL DEFAULT 'tushare'",
        "revision": "INTEGER NOT NULL DEFAULT 0",
        "is_legacy": "INTEGER NOT NULL DEFAULT 0",
    })
    # 存量标记：按 trade_date 分批（每批 ~5400 行），只处理未标记行（幂等）
    dates = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM daily ORDER BY trade_date")]
    if not dates:
        return
    now = _now_iso()
    for d in dates:
        nxt = _next_open_date(conn, d)
        avail = f"{nxt} 09:30:00+08:00"
        conn.execute(
            "UPDATE daily SET source='legacy_backfill', is_legacy=1, revision=0,"
            " ingested_at=?, available_at=? WHERE trade_date=? AND is_legacy=0",
            (now, avail, d),
        )


# ── M002：交易日历 ──

def mig_trade_cal(conn: sqlite3.Connection) -> None:
    """M002：建 trade_cal 表（数据由 paper_trading.cal.refresh_trade_cal 填充）。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trade_cal ("
        "  cal_date TEXT PRIMARY KEY,"
        "  is_open INTEGER NOT NULL CHECK (is_open IN (0,1)),"
        "  source TEXT NOT NULL DEFAULT 'tushare' CHECK (source IN ('tushare','local_infer')),"
        "  updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_cal_open ON trade_cal(is_open);")


# ── M003：instrument 交易规则 ──

def mig_instrument_rules(conn: sqlite3.Connection) -> None:
    """M003：建 instrument_rules 表（默认规则由 paper_trading.rules.default_rule 生成）。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS instrument_rules ("
        "  ts_code TEXT PRIMARY KEY,"
        "  inst_type TEXT NOT NULL CHECK (inst_type IN ('STOCK','ETF')),"
        "  commission_bps INTEGER NOT NULL DEFAULT 5,"
        "  min_commission_fen INTEGER NOT NULL DEFAULT 500,"
        "  sell_tax_bps INTEGER NOT NULL DEFAULT 10,"
        "  other_fee_bps INTEGER NOT NULL DEFAULT 1,"
        "  slippage_bps INTEGER NOT NULL DEFAULT 10,"
        "  lot_size INTEGER NOT NULL DEFAULT 100,"
        "  updated_at TEXT NOT NULL,"
        "  CHECK (commission_bps >= 0 AND min_commission_fen >= 0 AND sell_tax_bps >= 0"
        "         AND other_fee_bps >= 0 AND slippage_bps >= 0 AND lot_size > 0)"
        ")"
    )


# ── M004：领域表 ──

def mig_paper_tables(conn: sqlite3.Connection) -> None:
    """M004：建全部 pt_* 领域表（逐条 execute 保持事务原子性）。"""
    for sql in paper_ddl_statements():
        conn.execute(sql)


# ── 迁移注册表（版本单调递增，禁止重排/删除已发布版本） ──

MIGRATIONS: list[tuple[int, str, MigrationFn]] = [
    (1, "M001_daily_metadata", mig_daily_metadata),
    (2, "M002_trade_cal", mig_trade_cal),
    (3, "M003_instrument_rules", mig_instrument_rules),
    (4, "M004_paper_tables", mig_paper_tables),
]


def run_migrations(db_path: str | Path, verbose: bool = False) -> list[int]:
    """执行缺失的迁移，返回本次应用的版本列表（空 = 无需迁移）。"""
    db_path = Path(db_path)
    # 确保 schema_version 表存在（普通事务即可，不依赖迁移）
    with tx(db_path, immediate=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
              version   INTEGER PRIMARY KEY,
              name      TEXT NOT NULL,
              checksum  TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
        """)
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_version").fetchall()}

    applied_now: list[int] = []
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        src = inspect.getsource(fn)
        checksum = hashlib.sha1(src.encode("utf-8")).hexdigest()
        with tx(db_path, immediate=True) as conn:
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, name, checksum, applied_at) VALUES (?,?,?,?)",
                (version, name, checksum, _now_iso()),
            )
        applied_now.append(version)
        if verbose:
            print(f"[migrate] 应用 {name} (v{version})")
    return applied_now


def current_schema_version(db_path: str | Path) -> int:
    """当前库 schema 版本（0 = 未迁移）。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        try:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] or 0) if row else 0


def list_paper_tables(db_path: str | Path) -> set[str]:
    """列出库内已存在的领域表。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows} & set(PAPER_TABLE_NAMES)
