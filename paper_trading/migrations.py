"""增量迁移机制：schema_version 版本表 + 有序迁移列表。

特性：
  - 空库：无 schema_version → 全部迁移按序执行
  - 已有库（含 938MB 现网库）：只执行缺失版本
  - 重复执行：版本全在 → no-op（幂等）
  - 每个迁移独立 BEGIN IMMEDIATE 事务；迁移函数内部再叠 PRAGMA 检列，双重幂等
  - 迁移仅前向、保留业务数据；SQLite CHECK 变更允许在同一事务内重建内部领域表
"""
from __future__ import annotations

import hashlib
import inspect
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
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


# ── M005：pt_order 状态机补 CANCELLED（SQLite 无法 ALTER CHECK，重建表） ──

_PT_ORDER_NEW_DDL = (
    "CREATE TABLE pt_order_new ("
    " order_id TEXT PRIMARY KEY,"
    " idempotency_key TEXT NOT NULL UNIQUE,"
    " account_id INTEGER NOT NULL REFERENCES pt_account(account_id),"
    " source TEXT NOT NULL,"
    " ts_code TEXT NOT NULL,"
    " side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),"
    " qty INTEGER NOT NULL CHECK (qty > 0),"
    " state TEXT NOT NULL CHECK (state IN ('DRAFT','CONFIRMED','QUEUED','FILLED',"
    "   'PARTIALLY_FILLED_EXPIRED','EXPIRED','REJECTED','CANCELLED')),"
    " reserve_fen INTEGER NOT NULL DEFAULT 0 CHECK (reserve_fen >= 0),"
    " reject_reason TEXT,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT NOT NULL"
    ")"
)


def mig_order_cancelled_state(conn: sqlite3.Connection) -> None:
    """M005：pt_order 状态机加入 CANCELLED（重建表保留数据）。"""
    if not _table_exists(conn, "pt_order"):
        return
    # 检查现有 CHECK 是否已含 CANCELLED（幂等）
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='pt_order'"
    ).fetchone()
    if sql and "CANCELLED" in (sql[0] or ""):
        return
    # 重建：拷贝数据 → drop 旧表 → 建新表
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(_PT_ORDER_NEW_DDL)
    conn.execute(
        "INSERT INTO pt_order_new (order_id, idempotency_key, account_id, source,"
        " ts_code, side, qty, state, reserve_fen, reject_reason, created_at, updated_at)"
        " SELECT order_id, idempotency_key, account_id, source, ts_code, side, qty,"
        " state, reserve_fen, reject_reason, created_at, updated_at FROM pt_order"
    )
    conn.execute("DROP TABLE pt_order")
    conn.execute("ALTER TABLE pt_order_new RENAME TO pt_order")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pt_order_acct ON pt_order(account_id, state)"
    )
    conn.execute("PRAGMA foreign_keys=ON")


# ── M006：验收纠错所需的时点、预留、阻断与幂等字段 ──

def mig_acceptance_controls(conn: sqlite3.Connection) -> None:
    """M006：只新增字段/表，补齐交易时点、资产预留和审计门禁。"""
    if _table_exists(conn, "daily"):
        _ensure_columns(conn, "daily", {"effective_at": "TEXT"})
        conn.execute(
            "UPDATE daily SET effective_at=substr(trade_date,1,4)||'-'||"
            "substr(trade_date,5,2)||'-'||substr(trade_date,7,2)||'T15:00:00+08:00' "
            "WHERE effective_at IS NULL"
        )
    if _table_exists(conn, "pt_signal_snapshot"):
        _ensure_columns(conn, "pt_signal_snapshot", {
            "effective_at": "TEXT",
            "ingested_at": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'scan_result'",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "tradeable": "INTEGER NOT NULL DEFAULT 1",
        })
        conn.execute(
            "UPDATE pt_signal_snapshot SET effective_at=COALESCE(effective_at, available_at),"
            " ingested_at=COALESCE(ingested_at, available_at)"
        )
    if _table_exists(conn, "pt_order"):
        _ensure_columns(conn, "pt_order", {
            "reserved_qty": "INTEGER NOT NULL DEFAULT 0",
            "signal_trade_date": "TEXT",
            "confirmed_at": "TEXT",
            "eligible_trade_date": "TEXT",
        })
    if _table_exists(conn, "pt_cycle"):
        _ensure_columns(conn, "pt_cycle", {"blocked_reason": "TEXT"})
    if _table_exists(conn, "pt_corporate_action"):
        _ensure_columns(conn, "pt_corporate_action", {
            "status": "TEXT NOT NULL DEFAULT 'PENDING'",
            "applied_at": "TEXT",
            "adjustment_ref": "TEXT",
        })
    if _table_exists(conn, "pt_gate_report"):
        _ensure_columns(conn, "pt_gate_report", {
            "report_json": "TEXT",
            "code_version": "TEXT",
            "config_hash": "TEXT",
            "report_sha256": "TEXT",
        })
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pt_api_idempotency ("
        " idempotency_key TEXT PRIMARY KEY,"
        " operation TEXT NOT NULL,"
        " request_hash TEXT NOT NULL,"
        " state TEXT NOT NULL CHECK (state IN ('PROCESSING','COMPLETED')),"
        " status_code INTEGER,"
        " response_json TEXT,"
        " created_at TEXT NOT NULL,"
        " completed_at TEXT)"
    )


# ── M007：允许持仓批次在完整核销后归零 ──

_PT_POSITION_LOT_V7_DDL = (
    "CREATE TABLE pt_position_lot_v7 ("
    " lot_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " account_id INTEGER NOT NULL REFERENCES pt_account(account_id),"
    " ts_code TEXT NOT NULL,"
    " buy_fill_id TEXT NOT NULL REFERENCES pt_fill(fill_id),"
    " remaining_qty INTEGER NOT NULL CHECK (remaining_qty >= 0),"
    " cost_price_micro INTEGER NOT NULL CHECK (cost_price_micro > 0),"
    " sellable_date TEXT NOT NULL,"
    " created_at TEXT NOT NULL)"
)


def mig_position_lot_zero_balance(conn: sqlite3.Connection) -> None:
    """M007：保留完整批次审计记录，并允许全部卖出后的余额为零。"""
    if not _table_exists(conn, "pt_position_lot"):
        return
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='pt_position_lot'"
    ).fetchone()
    table_sql = (row[0] or "") if row else ""
    normalized_sql = "".join(table_sql.split()).lower()
    if "check(remaining_qty>=0)" in normalized_sql:
        return

    # SQLite 不支持直接修改 CHECK；采用同事务内的数据保留重建。
    conn.execute(_PT_POSITION_LOT_V7_DDL)
    conn.execute(
        "INSERT INTO pt_position_lot_v7 (lot_id, account_id, ts_code, buy_fill_id,"
        " remaining_qty, cost_price_micro, sellable_date, created_at)"
        " SELECT lot_id, account_id, ts_code, buy_fill_id, remaining_qty,"
        " cost_price_micro, sellable_date, created_at FROM pt_position_lot"
    )
    conn.execute("DROP TABLE pt_position_lot")
    conn.execute("ALTER TABLE pt_position_lot_v7 RENAME TO pt_position_lot")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pt_lot_code ON pt_position_lot(ts_code, sellable_date)"
    )


# ── M008：修复 v6 以后新增日线的 PIT 元数据 ──

def mig_daily_point_in_time_metadata(conn: sqlite3.Connection) -> None:
    """M008：幂等补齐日线时点字段，不改变行情数值。"""
    if not _table_exists(conn, "daily"):
        return
    _ensure_columns(conn, "daily", {
        "effective_at": "TEXT",
        "available_at": "TEXT",
        "ingested_at": "TEXT",
        "source": "TEXT NOT NULL DEFAULT 'tushare'",
        "revision": "INTEGER NOT NULL DEFAULT 1",
        "is_legacy": "INTEGER NOT NULL DEFAULT 0",
    })
    now = _now_iso()
    conn.execute(
        "UPDATE daily SET effective_at=substr(trade_date,1,4)||'-'||"
        "substr(trade_date,5,2)||'-'||substr(trade_date,7,2)||'T15:00:00+08:00' "
        "WHERE effective_at IS NULL OR trim(effective_at)=''"
    )
    conn.execute(
        "UPDATE daily SET ingested_at=? WHERE ingested_at IS NULL OR trim(ingested_at)=''",
        (now,),
    )
    conn.execute(
        "UPDATE daily SET available_at=ingested_at "
        "WHERE available_at IS NULL OR trim(available_at)=''"
    )
    conn.execute(
        "UPDATE daily SET source=CASE WHEN COALESCE(is_legacy,0)=1 "
        "THEN 'legacy_backfill' ELSE 'tushare' END "
        "WHERE source IS NULL OR trim(source)=''"
    )
    conn.execute("UPDATE daily SET revision=0 WHERE revision IS NULL")
    conn.execute("UPDATE daily SET is_legacy=0 WHERE is_legacy IS NULL")


def mig_execution_lineage(conn: sqlite3.Connection) -> None:
    """M009（P2.3）：pt_fill 固化执行血缘列（fee_breakdown/版本/参与率/quote 时点/input hash）。

    历史成交不重写；异常时停止新撮合，以 model version 区分；现金流水不回滚。
    """
    if not _table_exists(conn, "pt_fill"):
        return
    _ensure_columns(conn, "pt_fill", {
        "other_fee_fen": "INTEGER NOT NULL DEFAULT 0 CHECK (other_fee_fen >= 0)",
        "fee_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
        "cost_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
        "participation_bps": "INTEGER NOT NULL DEFAULT 500",
        "quote_available_at": "TEXT NOT NULL DEFAULT ''",
        "input_hash": "TEXT NOT NULL DEFAULT ''",
        "rule_version": "TEXT NOT NULL DEFAULT 'v1'",
    })
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_fill_input_hash ON pt_fill(input_hash)")


# ── 迁移注册表（版本单调递增，禁止重排/删除已发布版本） ──

MIGRATIONS: list[tuple[int, str, MigrationFn]] = [
    (1, "M001_daily_metadata", mig_daily_metadata),
    (2, "M002_trade_cal", mig_trade_cal),
    (3, "M003_instrument_rules", mig_instrument_rules),
    (4, "M004_paper_tables", mig_paper_tables),
    (5, "M005_order_cancelled_state", mig_order_cancelled_state),
    (6, "M006_acceptance_controls", mig_acceptance_controls),
    (7, "M007_position_lot_zero_balance", mig_position_lot_zero_balance),
    (8, "M008_daily_point_in_time_metadata", mig_daily_point_in_time_metadata),
    (9, "M009_execution_lineage", mig_execution_lineage),
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
