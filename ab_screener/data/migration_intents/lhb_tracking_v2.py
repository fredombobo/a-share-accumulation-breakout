"""迁移意图：龙虎榜席位级追踪（T01）。

新 id `v2:lhb_tracking`，不改已发布 intent，避免 checksum 漂移。
- PIT 原始表：top_inst_history / hm_list_history / lhb_official_raw_history
- 标准事实：事件、席位金额、买卖榜排名分表
- 主数据：席位、别名、身份假设（SCD2，只追加 revision / 有效期）
- 研究产物：对账、特征快照、信号观察与结果
- 抓取清单：带 VALID_EMPTY / NOT_PUBLISHED / FETCH_FAILED / DEGRADED / COMPLETE

金额列统一为整数分（fen），领域口径为人民币元。时间列为 +08:00 文本。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration
from ab_screener.domain.lhb_contracts import (
    ACTOR_TYPE_VALUES,
    CONFLICT_STATUS_VALUES,
    EVIDENCE_GRADE_VALUES,
    EXCHANGE_VALUES,
    FEATURE_WINDOWS,
    OFFICIAL_TAG_VALUES,
    OUTCOME_HORIZONS,
    OUTCOME_STATUS_VALUES,
    RANK_SIDE_VALUES,
    RAW_SIDE_VALUES,
    RECON_DIFF_VALUES,
    RECON_STATUS_VALUES,
    SIGNAL_STATUS_VALUES,
    SOURCE_STATUS_VALUES,
    WINDOW_CODE_VALUES,
    sql_enum,
)

_MIGRATION_ID = "v2:lhb_tracking"

LHB_PIT_HISTORY_TABLES: dict[str, list[str]] = {
    "top_inst_history": ["ts_code", "trade_date", "exalter", "reason", "side"],
    "hm_list_history": ["hm_name", "list_date"],
    "lhb_official_raw_history": ["exchange", "trade_date", "dataset"],
}

LHB_FACT_TABLES = (
    "lhb_ingest_manifests",
    "lhb_event",
    "lhb_seat_trade",
    "lhb_seat_rank",
    "seat_master",
    "seat_alias",
    "actor_master",
    "seat_actor_hypothesis",
    "lhb_reconciliation",
    "lhb_feature_snapshot",
    "lhb_signal_observation",
    "lhb_signal_outcome",
)

APPEND_ONLY_TABLES = tuple(LHB_PIT_HISTORY_TABLES) + LHB_FACT_TABLES


def _append_only_triggers(table: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
      BEFORE UPDATE ON {table}
      BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
      BEFORE DELETE ON {table}
      BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
    """


def _pit_ddl(table: str, key_cols: list[str]) -> str:
    pk_cols = ", ".join(key_cols + ["revision"])
    extra_check = ""
    if table == "top_inst_history":
        extra_check = f" CHECK (side IN {sql_enum(RAW_SIDE_VALUES)})"
    elif table == "lhb_official_raw_history":
        extra_check = f" CHECK (exchange IN {sql_enum(EXCHANGE_VALUES)})"
    col_sql = ", ".join(f"{col} TEXT NOT NULL" for col in key_cols)
    if table == "top_inst_history":
        col_sql = (
            "ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, exalter TEXT NOT NULL, "
            f"reason TEXT NOT NULL, side TEXT NOT NULL{extra_check}"
        )
    elif table == "lhb_official_raw_history":
        col_sql = (
            f"exchange TEXT NOT NULL{extra_check}, trade_date TEXT NOT NULL, "
            "dataset TEXT NOT NULL"
        )
    return f"""
    CREATE TABLE IF NOT EXISTS {table} (
      {col_sql},
      revision INTEGER NOT NULL CHECK (revision >= 1),
      available_at TEXT NOT NULL,
      source TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      PRIMARY KEY ({pk_cols})
    );
    CREATE INDEX IF NOT EXISTS idx_{table}_key
      ON {table}({", ".join(key_cols)}, available_at);
    {_append_only_triggers(table)}
    """


def apply_lhb_tracking(conn: sqlite3.Connection) -> None:
    for table, key_cols in LHB_PIT_HISTORY_TABLES.items():
        conn.executescript(_pit_ddl(table, key_cols))
    windows = "(" + ",".join(str(days) for days in FEATURE_WINDOWS) + ")"
    horizons = "(" + ",".join(str(days) for days in OUTCOME_HORIZONS) + ")"
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS lhb_ingest_manifests (
          manifest_id TEXT NOT NULL,
          dataset TEXT NOT NULL,
          partition_key TEXT NOT NULL,
          source TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          source_status TEXT NOT NULL CHECK (source_status IN {sql_enum(SOURCE_STATUS_VALUES)}),
          row_count INTEGER NOT NULL CHECK (row_count >= 0),
          content_sha256 TEXT NOT NULL,
          error_reason TEXT,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (manifest_id),
          UNIQUE (dataset, partition_key, source, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_manifest_part
          ON lhb_ingest_manifests(dataset, partition_key, source_status);
        {_append_only_triggers("lhb_ingest_manifests")}

        CREATE TABLE IF NOT EXISTS lhb_event (
          event_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          exchange TEXT NOT NULL CHECK (exchange IN {sql_enum(EXCHANGE_VALUES)}),
          ts_code TEXT NOT NULL,
          window_code TEXT NOT NULL CHECK (window_code IN {sql_enum(WINDOW_CODE_VALUES)}),
          reason_code TEXT NOT NULL,
          reason_raw TEXT NOT NULL,
          reason_catalog_version TEXT NOT NULL,
          disclose_date TEXT NOT NULL,
          period_start TEXT,
          period_end TEXT,
          flow_fingerprint TEXT,
          source TEXT NOT NULL,
          source_status TEXT NOT NULL CHECK (source_status IN {sql_enum(SOURCE_STATUS_VALUES)}),
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          PRIMARY KEY (event_id, revision),
          UNIQUE (exchange, ts_code, window_code, reason_code, disclose_date, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_event_code_date
          ON lhb_event(ts_code, disclose_date, window_code);
        {_append_only_triggers("lhb_event")}

        CREATE TABLE IF NOT EXISTS lhb_seat_trade (
          event_id TEXT NOT NULL,
          seat_raw TEXT NOT NULL,
          seat_id TEXT,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          buy_amount_fen INTEGER NOT NULL CHECK (buy_amount_fen >= 0),
          sell_amount_fen INTEGER NOT NULL CHECK (sell_amount_fen >= 0),
          net_amount_fen INTEGER NOT NULL,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (event_id, seat_raw, revision),
          CHECK (net_amount_fen = buy_amount_fen - sell_amount_fen)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_seat_trade_seat
          ON lhb_seat_trade(seat_raw, event_id);
        {_append_only_triggers("lhb_seat_trade")}

        CREATE TABLE IF NOT EXISTS lhb_seat_rank (
          event_id TEXT NOT NULL,
          seat_raw TEXT NOT NULL,
          seat_id TEXT,
          side TEXT NOT NULL CHECK (side IN {sql_enum(RANK_SIDE_VALUES)}),
          rank_no INTEGER NOT NULL CHECK (rank_no >= 1),
          revision INTEGER NOT NULL CHECK (revision >= 1),
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (event_id, seat_raw, side, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_seat_rank_event
          ON lhb_seat_rank(event_id, side, rank_no);
        {_append_only_triggers("lhb_seat_rank")}

        CREATE TABLE IF NOT EXISTS seat_master (
          seat_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          canonical_name TEXT NOT NULL,
          official_tag TEXT NOT NULL CHECK (official_tag IN {sql_enum(OFFICIAL_TAG_VALUES)}),
          broker_name TEXT,
          branch_city TEXT,
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (seat_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_seat_master_name
          ON seat_master(canonical_name, valid_from);
        {_append_only_triggers("seat_master")}

        CREATE TABLE IF NOT EXISTS seat_alias (
          alias_raw TEXT NOT NULL,
          seat_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (alias_raw, seat_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_seat_alias_raw ON seat_alias(alias_raw, valid_from);
        {_append_only_triggers("seat_alias")}

        CREATE TABLE IF NOT EXISTS actor_master (
          actor_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          actor_type TEXT NOT NULL CHECK (actor_type IN {sql_enum(ACTOR_TYPE_VALUES)}),
          display_name TEXT NOT NULL,
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (actor_id, revision)
        );
        {_append_only_triggers("actor_master")}

        CREATE TABLE IF NOT EXISTS seat_actor_hypothesis (
          seat_id TEXT NOT NULL,
          actor_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
          evidence_grade TEXT NOT NULL CHECK (evidence_grade IN {sql_enum(EVIDENCE_GRADE_VALUES)}),
          evidence_source TEXT NOT NULL,
          conflict_status TEXT NOT NULL CHECK (
            conflict_status IN {sql_enum(CONFLICT_STATUS_VALUES)}
          ),
          hypothesis_note TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (seat_id, actor_id, valid_from, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_seat_actor_asof
          ON seat_actor_hypothesis(seat_id, valid_from, valid_to);
        {_append_only_triggers("seat_actor_hypothesis")}

        CREATE TABLE IF NOT EXISTS lhb_reconciliation (
          recon_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          trade_date TEXT NOT NULL,
          ts_code TEXT,
          field_name TEXT NOT NULL,
          left_source TEXT NOT NULL,
          left_value TEXT,
          right_source TEXT NOT NULL,
          right_value TEXT,
          diff_type TEXT NOT NULL CHECK (diff_type IN {sql_enum(RECON_DIFF_VALUES)}),
          status TEXT NOT NULL CHECK (status IN {sql_enum(RECON_STATUS_VALUES)}),
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (recon_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_recon_date
          ON lhb_reconciliation(trade_date, status, diff_type);
        {_append_only_triggers("lhb_reconciliation")}

        CREATE TABLE IF NOT EXISTS lhb_feature_snapshot (
          snapshot_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          as_of TEXT NOT NULL,
          available_at TEXT NOT NULL,
          subject_type TEXT NOT NULL CHECK (subject_type IN ('seat','actor','stock','board')),
          subject_id TEXT NOT NULL,
          window_days INTEGER NOT NULL CHECK (window_days IN {windows}),
          model_version TEXT NOT NULL,
          sample_size INTEGER,
          source TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          PRIMARY KEY (snapshot_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_feat_subject
          ON lhb_feature_snapshot(subject_type, subject_id, as_of);
        {_append_only_triggers("lhb_feature_snapshot")}

        CREATE TABLE IF NOT EXISTS lhb_signal_observation (
          observation_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          ts_code TEXT NOT NULL,
          signal_date TEXT NOT NULL,
          disclose_at TEXT NOT NULL,
          earliest_executable_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN {sql_enum(SIGNAL_STATUS_VALUES)}),
          research_only INTEGER NOT NULL DEFAULT 1 CHECK (research_only = 1),
          scores_json TEXT NOT NULL,
          veto_codes_json TEXT NOT NULL DEFAULT '[]',
          policy_version TEXT NOT NULL,
          data_version TEXT NOT NULL,
          identity_version TEXT NOT NULL,
          feature_snapshot_id TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (observation_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_signal_date
          ON lhb_signal_observation(signal_date, status, ts_code);
        {_append_only_triggers("lhb_signal_observation")}

        CREATE TABLE IF NOT EXISTS lhb_signal_outcome (
          observation_id TEXT NOT NULL,
          horizon_days INTEGER NOT NULL CHECK (horizon_days IN {horizons}),
          revision INTEGER NOT NULL CHECK (revision >= 1),
          status TEXT NOT NULL CHECK (status IN {sql_enum(OUTCOME_STATUS_VALUES)}),
          entry_fillable INTEGER,
          gross_return REAL,
          net_return REAL,
          benchmark_excess REAL,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (observation_id, horizon_days, revision)
        );
        {_append_only_triggers("lhb_signal_outcome")}
        """
    )


def register_lhb_tracking_migration() -> None:
    if getattr(register_lhb_tracking_migration, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID,
        apply_lhb_tracking,
        depends_on=("v2:pit_history", "v2:aux_history"),
    )
    register_lhb_tracking_migration._registered = True  # type: ignore[attr-defined]


register_lhb_tracking_migration()
