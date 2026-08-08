"""领域表 DDL 常量 + 表名白名单。

约定：
  - 金额存「分」（整数）；价格存「微元」（整数，1元 = 1_000_000）；数量整数股
  - 时间一律 ISO8601 含 +08:00（Asia/Shanghai）
  - 写表预留 idempotency_key TEXT UNIQUE（幂等保护）
  - 字段级 CHECK 防浮点/负值；REFERENCES 防未知规则/状态
"""
from __future__ import annotations

PAPER_TABLE_NAMES: frozenset[str] = frozenset({
    "pt_account", "pt_signal_snapshot", "pt_order", "pt_fill", "pt_cash_flow",
    "pt_position_lot", "pt_daily_snapshot", "pt_cycle", "pt_reconciliation",
    "pt_corporate_action", "pt_audit_event", "pt_gate_report",
    "pt_api_idempotency", "trade_cal", "instrument_rules",
})

# DDL 语句列表：逐条 execute（不能用 executescript，它会隐式 COMMIT 破坏迁移事务原子性）
_DDL_STATEMENTS: list[str] = []


def _stmt(sql: str) -> None:
    _DDL_STATEMENTS.append(sql.strip())


_stmt("""CREATE TABLE IF NOT EXISTS pt_account (
  account_id INTEGER PRIMARY KEY CHECK (account_id = 1),
  initial_cash_fen INTEGER NOT NULL CHECK (initial_cash_fen >= 0),
  status TEXT NOT NULL DEFAULT 'ACTIVE'
          CHECK (status IN ('ACTIVE','FROZEN','CLOSED')),
  config_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);""")

_stmt("""CREATE TABLE IF NOT EXISTS pt_signal_snapshot (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  pool TEXT NOT NULL CHECK (pool IN ('A','B')),
  total_score REAL,
  suggested_pos_pct REAL CHECK (suggested_pos_pct IS NULL OR (suggested_pos_pct BETWEEN 0 AND 100)),
  strategy_version TEXT,
  input_hash TEXT NOT NULL,
  effective_at TEXT,
  available_at TEXT NOT NULL,
  ingested_at TEXT,
  source TEXT NOT NULL DEFAULT 'scan_result',
  revision INTEGER NOT NULL DEFAULT 1,
  tradeable INTEGER NOT NULL DEFAULT 1 CHECK (tradeable IN (0,1)),
  PRIMARY KEY (trade_date, ts_code, pool)
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_sig_date ON pt_signal_snapshot(trade_date);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_order (
  order_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  account_id INTEGER NOT NULL REFERENCES pt_account(account_id),
  source TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  qty INTEGER NOT NULL CHECK (qty > 0),
  state TEXT NOT NULL CHECK (state IN ('DRAFT','CONFIRMED','QUEUED','FILLED',
        'PARTIALLY_FILLED_EXPIRED','EXPIRED','REJECTED','CANCELLED')),
  reserve_fen INTEGER NOT NULL DEFAULT 0 CHECK (reserve_fen >= 0),
  reserved_qty INTEGER NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
  signal_trade_date TEXT,
  confirmed_at TEXT,
  eligible_trade_date TEXT,
  reject_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_order_acct ON pt_order(account_id, state);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_fill (
  fill_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES pt_order(order_id),
  ref_open_price_micro INTEGER NOT NULL CHECK (ref_open_price_micro > 0),
  fill_price_micro INTEGER NOT NULL CHECK (fill_price_micro > 0),
  qty INTEGER NOT NULL CHECK (qty > 0),
  commission_fen INTEGER NOT NULL DEFAULT 0 CHECK (commission_fen >= 0),
  tax_fen INTEGER NOT NULL DEFAULT 0 CHECK (tax_fen >= 0),
  fill_model_version TEXT NOT NULL,
  quote_revision TEXT NOT NULL,
  filled_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_fill_order ON pt_fill(order_id);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_cash_flow (
  flow_id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES pt_account(account_id),
  kind TEXT NOT NULL CHECK (kind IN ('INITIAL','BUY','SELL','COMMISSION','TAX',
        'CORPORATE_ACTION','MANUAL')),
  amount_fen INTEGER NOT NULL CHECK (amount_fen != 0),
  balance_fen INTEGER NOT NULL CHECK (balance_fen >= 0),
  ref_id TEXT,
  occurred_at TEXT NOT NULL,
  CHECK (amount_fen = CAST(amount_fen AS INTEGER))
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_cash_acct ON pt_cash_flow(account_id, occurred_at);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_position_lot (
  lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES pt_account(account_id),
  ts_code TEXT NOT NULL,
  buy_fill_id TEXT NOT NULL REFERENCES pt_fill(fill_id),
  remaining_qty INTEGER NOT NULL CHECK (remaining_qty >= 0),
  cost_price_micro INTEGER NOT NULL CHECK (cost_price_micro > 0),
  sellable_date TEXT NOT NULL,
  created_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_lot_code ON pt_position_lot(ts_code, sellable_date);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_daily_snapshot (
  account_id INTEGER NOT NULL,
  trade_date TEXT NOT NULL,
  cash_fen INTEGER NOT NULL CHECK (cash_fen >= 0),
  market_value_fen INTEGER NOT NULL CHECK (market_value_fen >= 0),
  total_asset_fen INTEGER NOT NULL CHECK (total_asset_fen >= 0),
  realized_pnl_fen INTEGER,
  unrealized_pnl_fen INTEGER,
  drawdown_fen INTEGER CHECK (drawdown_fen IS NULL OR drawdown_fen <= 0),
  positions_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (account_id, trade_date)
);""")

_stmt("""CREATE TABLE IF NOT EXISTS pt_cycle (
  cycle_id TEXT PRIMARY KEY,
  run_date TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('PRE_OPEN','RESERVE','EXECUTE','SETTLE',
        'RECONCILE','DONE')),
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  data_version TEXT,
  blocked_reason TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_cycle_date ON pt_cycle(run_date);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_reconciliation (
  rec_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('OK','DIFF','ERROR')),
  diff_json TEXT NOT NULL DEFAULT '[]',
  severity TEXT CHECK (severity IN ('INFO','WARN','CRITICAL')),
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','ESCALATED')),
  checked_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_rec_date ON pt_reconciliation(run_date);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_corporate_action (
  action_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_code TEXT NOT NULL,
  ex_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('DIVIDEND','SPLIT','RIGHT','CONVERT')),
  amount_fen INTEGER,
  ratio REAL,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPLIED')),
  applied_at TEXT,
  adjustment_ref TEXT,
  UNIQUE (ts_code, ex_date, kind)
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_ca_code ON pt_corporate_action(ts_code, ex_date);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_audit_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  before_json TEXT,
  after_json TEXT,
  occurred_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_pt_audit_entity ON pt_audit_event(entity_type, entity_id);")

_stmt("""CREATE TABLE IF NOT EXISTS pt_gate_report (
  report_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL,
  passed INTEGER NOT NULL CHECK (passed IN (0,1)),
  data_version TEXT NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  report_json TEXT,
  code_version TEXT,
  config_hash TEXT,
  report_sha256 TEXT,
  generated_at TEXT NOT NULL
);""")

_stmt("""CREATE TABLE IF NOT EXISTS pt_api_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PROCESSING','COMPLETED')),
  status_code INTEGER,
  response_json TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);""")

_stmt("""CREATE TABLE IF NOT EXISTS trade_cal (
  cal_date TEXT PRIMARY KEY,
  is_open INTEGER NOT NULL CHECK (is_open IN (0,1)),
  source TEXT NOT NULL DEFAULT 'tushare' CHECK (source IN ('tushare','local_infer')),
  updated_at TEXT NOT NULL
);""")
_stmt("CREATE INDEX IF NOT EXISTS idx_trade_cal_open ON trade_cal(is_open);")

_stmt("""CREATE TABLE IF NOT EXISTS instrument_rules (
  ts_code TEXT PRIMARY KEY,
  inst_type TEXT NOT NULL CHECK (inst_type IN ('STOCK','ETF')),
  commission_bps INTEGER NOT NULL DEFAULT 5,
  min_commission_fen INTEGER NOT NULL DEFAULT 500,
  sell_tax_bps INTEGER NOT NULL DEFAULT 10,
  other_fee_bps INTEGER NOT NULL DEFAULT 1,
  slippage_bps INTEGER NOT NULL DEFAULT 10,
  lot_size INTEGER NOT NULL DEFAULT 100,
  updated_at TEXT NOT NULL,
  CHECK (commission_bps >= 0 AND min_commission_fen >= 0 AND sell_tax_bps >= 0
         AND other_fee_bps >= 0 AND slippage_bps >= 0 AND lot_size > 0)
);""")


def paper_ddl_statements() -> list[str]:
    """领域 DDL 语句列表（不含 pt_order 等表——见 DDL 常量按需使用）。"""
    return list(_DDL_STATEMENTS)
