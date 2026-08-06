"""
本地 SQLite 存储层
==================
持久化 Tushare 数据到本地数据库，支持每日增量更新：
  - daily        个股日线（K线）
  - daily_basic  基本面指标（pe/pb/mv/turnover）
  - moneyflow    个股资金流
  - stock_basic  全市场股票列表

用法：
    from local_store import LocalStore
    store = LocalStore()
    store.upsert_daily(df)          # INSERT OR REPLACE
    df = store.load_daily('301498.SZ', start='20260601')
    store.sync_from_tushare()       # 增量同步（只拉最新）
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pandas as pd

_DB_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "runtime"
_DB_PATH = _DB_DIR / "stock_data.db"


# 允许的表名白名单（防注入）
_ALLOWED_TABLES = {"daily", "daily_basic", "moneyflow", "stock_basic", "fina_indicator", "scan_result",
                   "strategy_params", "param_eval"}


class LocalStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or _DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # 权衡：FastAPI 多线程下 sqlite3 连接默认 check_same_thread=True，不能跨线程
        # 复用；若做连接复用需加锁，风险高。故保持「每次调用新建连接」，通过
        # contextmanager + finally close 保证显式释放（原来只 commit 依赖 GC）。
        # PRAGMA journal_mode=WAL 为库级持久设置，重复执行开销极小，保留。
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                pre_close REAL, change REAL, pct_chg REAL,
                vol REAL, amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(trade_date);

            CREATE TABLE IF NOT EXISTS daily_basic (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL, pe REAL, pb REAL, ps_ttm REAL, dp REAL,
                total_mv REAL, circ_mv REAL, turnover_rate REAL, volume_ratio REAL,
                PRIMARY KEY (ts_code, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_dbbasic_date ON daily_basic(trade_date);

            CREATE TABLE IF NOT EXISTS moneyflow (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                buy_elg_amount REAL, buy_elg_vol REAL,
                buy_lg_amount REAL, buy_lg_vol REAL,
                buy_md_amount REAL, buy_md_vol REAL,
                buy_sm_amount REAL, buy_sm_vol REAL,
                net_mf_amount REAL, net_mf_vol REAL,
                sell_elg_amount REAL, sell_elg_vol REAL,
                sell_lg_amount REAL, sell_lg_vol REAL,
                sell_md_amount REAL, sell_md_vol REAL,
                sell_sm_amount REAL, sell_sm_vol REAL,
                PRIMARY KEY (ts_code, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow(trade_date);

            CREATE TABLE IF NOT EXISTS stock_basic (
                ts_code TEXT PRIMARY KEY,
                symbol TEXT, name TEXT, area TEXT, industry TEXT,
                market TEXT, list_date TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS fina_indicator (
                ts_code TEXT NOT NULL,
                ann_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                roe REAL, roe_waa REAL, roa REAL,
                grossprofit_margin REAL, netprofit_margin REAL,
                or_yoy REAL, netprofit_yoy REAL,
                debt_to_assets REAL, current_ratio REAL, quick_ratio REAL,
                ocf_to_or REAL, eps REAL, bps REAL,
                PRIMARY KEY (ts_code, ann_date)
            );
            CREATE INDEX IF NOT EXISTS idx_fina_ts ON fina_indicator(ts_code);
            CREATE INDEX IF NOT EXISTS idx_fina_ann ON fina_indicator(ann_date);

            CREATE TABLE IF NOT EXISTS scan_result (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT, industry TEXT,
                price REAL, mv_yi REAL, pe REAL, pb REAL, turnover REAL,
                box_days INTEGER, box_amp REAL, vol_ratio REAL,
                fund_net_wan REAL, fund_ratio REAL,
                signal_score REAL, fund_score REAL, basic_score REAL,
                total_score REAL,
                reasons TEXT, breakout_date TEXT,
                created_at TEXT,
                PRIMARY KEY (trade_date, ts_code)
            );
            CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_result(trade_date);

            CREATE TABLE IF NOT EXISTS strategy_params (
                param_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                params_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                is_profit_factor REAL, is_win_rate REAL, is_max_dd REAL,
                oos_profit_factor REAL, oos_win_rate REAL, oos_max_dd REAL,
                wf_pass INTEGER,
                seeded_at TEXT, promoted_at TEXT, retired_at TEXT,
                weekly_oos_pf REAL, degrade_streak INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sp_status ON strategy_params(status);

            CREATE TABLE IF NOT EXISTS param_eval (
                param_id TEXT NOT NULL,
                eval_kind TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT,
                n_trades INTEGER,
                win_rate REAL, profit_factor REAL, max_dd REAL,
                evaluated_at TEXT,
                PRIMARY KEY (param_id, eval_kind, window_start)
            );
            """)

    # ── 查询 ──

    def _check_table(self, table: str) -> None:
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"非法表名: {table}")

    def max_trade_date(self, table: str) -> str | None:
        self._check_table(table)
        with self._connect() as conn:
            row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
            return row[0] if row and row[0] else None

    def distinct_dates(self, table: str, limit: int | None = None) -> list[str]:
        """获取表内去重后的交易日（升序）。limit 限制返回最近 N 个。"""
        self._check_table(table)
        sql = f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        dates = [r[0] for r in rows]
        return dates[-limit:] if limit else dates

    def load_daily(self, ts_codes: list[str] | None = None,
                   start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self._load("daily", ts_codes, start, end)

    def load_daily_basic(self, ts_codes: list[str] | None = None,
                         start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self._load("daily_basic", ts_codes, start, end)

    def load_moneyflow(self, ts_codes: list[str] | None = None,
                       start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self._load("moneyflow", ts_codes, start, end)

    def load_stock_basic(self) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql("SELECT ts_code,symbol,name,area,industry,market,list_date FROM stock_basic", conn)

    def _load(self, table: str, ts_codes: list[str] | None,
              start: str | None, end: str | None) -> pd.DataFrame:
        self._check_table(table)
        sql = f"SELECT * FROM {table} WHERE 1=1"
        params: list = []
        if ts_codes:
            ph = ",".join("?" * len(ts_codes))
            sql += f" AND ts_code IN ({ph})"
            params.extend(ts_codes)
        if start:
            sql += " AND trade_date >= ?"
            params.append(start)
        if end:
            sql += " AND trade_date <= ?"
            params.append(end)
        sql += " ORDER BY ts_code, trade_date"
        with self._connect() as conn:
            return pd.read_sql(sql, conn, params=params)

    # ── 写入 ──

    def upsert_daily(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        cols = ["ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "change", "pct_chg", "vol", "amount"]
        cols = [c for c in cols if c in df.columns]
        return self._upsert("daily", df[cols])

    def upsert_daily_basic(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        cols = ["ts_code", "trade_date", "close", "pe", "pb", "ps_ttm", "dp",
                "total_mv", "circ_mv", "turnover_rate", "volume_ratio"]
        cols = [c for c in cols if c in df.columns]
        return self._upsert("daily_basic", df[cols])

    def upsert_moneyflow(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        cols = ["ts_code", "trade_date", "buy_elg_amount", "buy_elg_vol",
                "buy_lg_amount", "buy_lg_vol", "buy_md_amount", "buy_md_vol",
                "buy_sm_amount", "buy_sm_vol", "net_mf_amount", "net_mf_vol",
                "sell_elg_amount", "sell_elg_vol", "sell_lg_amount", "sell_lg_vol",
                "sell_md_amount", "sell_md_vol", "sell_sm_amount", "sell_sm_vol"]
        cols = [c for c in cols if c in df.columns]
        return self._upsert("moneyflow", df[cols])

    def upsert_stock_basic(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        df = df.copy()
        df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cols = ["ts_code", "symbol", "name", "area", "industry", "market", "list_date", "updated_at"]
        cols = [c for c in cols if c in df.columns]
        return self._upsert("stock_basic", df[cols])

    def upsert_fina_indicator(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        cols = ["ts_code", "ann_date", "end_date", "roe", "roe_waa", "roa",
                "grossprofit_margin", "netprofit_margin", "or_yoy", "netprofit_yoy",
                "debt_to_assets", "current_ratio", "quick_ratio", "ocf_to_or", "eps", "bps"]
        cols = [c for c in cols if c in df.columns]
        return self._upsert("fina_indicator", df[cols])

    def upsert_scan_result(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        df = df.copy()
        if "created_at" not in df.columns:
            df["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self._upsert("scan_result", df)

    def load_fina_indicator(self, ts_codes: list[str] | None = None, limit: int | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM fina_indicator"
        params: list = []
        if ts_codes:
            ph = ",".join("?" * len(ts_codes))
            sql += f" WHERE ts_code IN ({ph})"
            params.extend(ts_codes)
        sql += " ORDER BY ts_code, ann_date DESC"
        with self._connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if limit and ts_codes and len(ts_codes) == 1:
            return df.head(limit)
        return df

    def load_scan_result(self, trade_date: str | None = None) -> pd.DataFrame:
        """读取最近一次扫描结果（可指定交易日；默认取最新）"""
        if trade_date is None:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(trade_date) FROM scan_result").fetchone()
                trade_date = row[0] if row and row[0] else None
            if trade_date is None:
                return pd.DataFrame()
        with self._connect() as conn:
            return pd.read_sql(
                "SELECT * FROM scan_result WHERE trade_date=? ORDER BY total_score DESC",
                conn, params=[trade_date],
            )

    # ── 策略参数注册制（strategy_params / param_eval） ──

    def upsert_strategy_params(self, df: pd.DataFrame) -> int:
        return self._upsert("strategy_params", df)

    def load_strategy_params(self, status: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM strategy_params"
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY oos_profit_factor DESC NULLS LAST"
        # SQLite 不支持 NULLS LAST（旧版本），改用 CASE 排序
        sql = sql.replace(" ORDER BY oos_profit_factor DESC NULLS LAST",
                          " ORDER BY CASE WHEN oos_profit_factor IS NULL THEN 1 ELSE 0 END, oos_profit_factor DESC")
        with self._connect() as conn:
            return pd.read_sql(sql, conn, params=params)

    def update_strategy_status(self, param_id: str, status: str, **fields) -> None:
        """更新参数状态 + 可选字段（promoted_at/retired_at/weekly_oos_pf/degrade_streak 等）。"""
        sets = ["status=?"]
        vals: list = [status]
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(param_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE strategy_params SET {', '.join(sets)} WHERE param_id=?", vals)

    def upsert_param_eval(self, df: pd.DataFrame) -> int:
        if "evaluated_at" not in df.columns:
            df = df.copy()
            df["evaluated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self._upsert("param_eval", df)

    def load_param_eval(self, param_id: str | None = None, eval_kind: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM param_eval WHERE 1=1"
        params: list = []
        if param_id:
            sql += " AND param_id=?"
            params.append(param_id)
        if eval_kind:
            sql += " AND eval_kind=?"
            params.append(eval_kind)
        with self._connect() as conn:
            return pd.read_sql(sql, conn, params=params)

    def _upsert(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        self._check_table(table)
        df = df.drop_duplicates()
        cols = list(df.columns)
        placeholders = ",".join("?" * len(cols))
        # DO UPDATE：只更新本次提供的列，避免 OR REPLACE 把未提供的列抹成 NULL
        if "trade_date" in cols and table != "stock_basic":
            pk = ["ts_code", "trade_date"]
            set_cols = [c for c in cols if c not in pk]
            if set_cols:
                set_clause = ",".join(f"{c}=excluded.{c}" for c in set_cols)
                upsert_sql = (
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT({','.join(pk)}) DO UPDATE SET {set_clause}"
                )
            else:
                # 仅含主键列时无列可更新，降级为 INSERT OR IGNORE（已有则跳过）
                upsert_sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        else:
            upsert_sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        rows = [tuple(None if pd.isna(x) else x for x in r) for r in df[cols].itertuples(index=False)]
        with self._connect() as conn:
            conn.executemany(upsert_sql, rows)
        return len(rows)


def db_path() -> Path:
    return _DB_PATH


def sync_fina_for_codes(
    ts_codes: list[str],
    periods: list[str] | None = None,
    verbose: bool = True,
) -> int:
    """为指定股票列表同步财务指标（只拉最近几期财报）。

    用于选股候选/关注列表的财报补充，避免全市场逐股拉取。
    返回新增行数。
    """
    import sys
    import time

    _here = os.path.dirname(os.path.abspath(__file__))
    # tushare_http.py 与本文件同目录，去掉硬编码绝对路径 E:\openclaw\stock_picker_cn
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from tushare_http import pro

    store = LocalStore()
    if periods is None:
        latest_td = store.max_trade_date("daily") or "20260731"
        year = int(latest_td[:4])
        month = int(latest_td[4:6])
        if month >= 10:
            periods = [f"{year}0930", f"{year}0630", f"{year}0331", f"{year-1}1231"]
        elif month >= 7:
            periods = [f"{year}0630", f"{year}0331", f"{year-1}1231", f"{year-1}0930"]
        elif month >= 4:
            periods = [f"{year}0331", f"{year-1}1231", f"{year-1}0930", f"{year-1}0630"]
        else:
            periods = [f"{year-1}1231", f"{year-1}0930", f"{year-1}0630", f"{year-1}0331"]

    existing = store.load_fina_indicator(ts_codes=ts_codes)
    # 拉取后按 end_date 过滤入库（upsert_fina_indicator 存 end_date），故「已存在」
    # 判定也统一用 end_date；原来用 ann_date 与 end_date 几乎永不相等导致全量重拉
    have_fina = set(zip(existing["ts_code"], existing["end_date"])) if not existing.empty else set()

    rows = 0
    for ts in ts_codes:
        need = [p for p in periods if (ts, p) not in have_fina]
        if not need:
            continue
        try:
            fi = pro.fina_indicator(
                ts_code=ts, period="", start_date=min(need)[:6] + "01",
                end_date=max(need)[:6] + "31",
                fields="ts_code,ann_date,end_date,roe,roe_waa,roa,grossprofit_margin,netprofit_margin,or_yoy,netprofit_yoy,debt_to_assets,current_ratio,quick_ratio,ocf_to_or,eps,bps",
            )
            if not fi.empty:
                fi = fi[fi["end_date"].astype(str).isin(need)]
                if not fi.empty:
                    store.upsert_fina_indicator(fi)
                    rows += len(fi)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  [warn] fina {ts}: {str(e)[:60]}")
            time.sleep(0.5)
        time.sleep(0.05)
    return rows


def sync_from_tushare(
    days_back: int = 300,
    moneyflow_days: int | None = None,
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """增量同步：从 Tushare 拉取并写入本地库，只拉库内缺失的交易日。

    - daily / daily_basic：从 max(库内日期, 今天-days_back) 到最新，逐日拉取
    - moneyflow：同样增量（moneyflow_days 控制回看，默认与 daily 一致）
    - stock_basic：每次刷新
    返回 {"daily_dates": [...], "moneyflow_dates": [...], "rows": {...}}
    """
    import sys
    from datetime import timedelta

    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from tushare_http import pro
    import time

    store = LocalStore()
    now = datetime.now()

    # ── 交易日历（覆盖到今天的开市日） ──
    cal_start = (now - timedelta(days=days_back * 2)).strftime("%Y%m%d")
    cal_end = now.strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="", start_date=cal_start, end_date=cal_end, fields="cal_date,is_open")
    open_dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())

    # ── stock_basic ──
    if verbose:
        print("[sync] 刷新股票列表…")
    basic = pro.stock_basic(exchange="", list_status="L",
                            fields="ts_code,symbol,name,area,industry,market,list_date")
    store.upsert_stock_basic(basic)

    # ── daily / daily_basic 增量（对比库内 DISTINCT 日期求差集，中间空洞也补） ──
    db_daily = set(store.distinct_dates("daily"))
    if force:
        new_dates = open_dates[-min(days_back, len(open_dates)):]
    else:
        new_dates = [d for d in open_dates if d not in db_daily]
    # 若库已全，保持最近 days_back 窗口不回退
    if not new_dates and not db_daily:
        new_dates = open_dates[-min(days_back, len(open_dates)):]
    if verbose:
        print(f"[sync] daily: 库内 {len(db_daily)} 日，需拉 {len(new_dates)} 个交易日（含空洞补缺）")

    daily_rows = dbbasic_rows = mf_rows = 0
    if new_dates:
        for i, d in enumerate(new_dates):
            try:
                dd = pro.daily(trade_date=d)
                if not dd.empty:
                    store.upsert_daily(dd)
                    daily_rows += len(dd)
                db = pro.daily_basic(trade_date=d, fields="ts_code,trade_date,close,pe,pb,ps_ttm,dp,total_mv,circ_mv,turnover_rate,volume_ratio")
                if not db.empty:
                    store.upsert_daily_basic(db)
                    dbbasic_rows += len(db)
                if verbose and (i + 1) % 10 == 0:
                    print(f"  ...已同步 {i+1}/{len(new_dates)} 日")
                time.sleep(0.1)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] {d} 同步失败: {str(e)[:80]}")
                time.sleep(1.0)

    # ── moneyflow 增量（同样对比 DISTINCT 日期求差集补洞） ──
    mf_lookback = moneyflow_days or days_back
    db_mf = set(store.distinct_dates("moneyflow"))
    if force:
        mf_dates = open_dates[-min(mf_lookback, len(open_dates)):]
    else:
        mf_dates = [d for d in open_dates if d not in db_mf]
    if not mf_dates and not db_mf:
        mf_dates = open_dates[-min(mf_lookback, len(open_dates)):]
    if verbose:
        print(f"[sync] moneyflow: 库内 {len(db_mf)} 日，需拉 {len(mf_dates)} 个交易日（含空洞补缺）")
    if mf_dates:
        for i, d in enumerate(mf_dates):
            try:
                m = pro.moneyflow(trade_date=d)
                if not m.empty:
                    store.upsert_moneyflow(m)
                    mf_rows += len(m)
                if verbose and (i + 1) % 5 == 0:
                    print(f"  ...资金流已同步 {i+1}/{len(mf_dates)} 日")
                time.sleep(0.1)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] {d} moneyflow 同步失败: {str(e)[:80]}")
                time.sleep(1.0)

    return {
        "daily_dates": new_dates,
        "moneyflow_dates": mf_dates,
        "rows": {"daily": daily_rows, "daily_basic": dbbasic_rows, "moneyflow": mf_rows, "fina": 0},
        "latest_daily": store.max_trade_date("daily"),
        "latest_moneyflow": store.max_trade_date("moneyflow"),
    }


if __name__ == "__main__":
    store = LocalStore()
    print("DB:", store.db_path)
    for t in ("daily", "daily_basic", "moneyflow"):
        mx = store.max_trade_date(t)
        print(f"  {t}: max_date={mx} rows={len(store._load(t, None, None, None))}")
    print("stock_basic rows:", len(store.load_stock_basic()))
    if os.environ.get("SYNC") == "1":
        res = sync_from_tushare(days_back=300, verbose=True)
        print("同步结果:", res)
