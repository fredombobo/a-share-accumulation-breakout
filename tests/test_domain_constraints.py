"""阶段1 验收：领域约束（非法状态 / 浮点金额 / 未知规则被拒）。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from paper_trading.migrations import run_migrations

_ACCOUNT_ROW = (
    1,          # account_id
    1_000_000,  # initial_cash_fen = 10000 元
    "ACTIVE",
    1,
    "2026-08-07T10:00:00+08:00",
    "2026-08-07T10:00:00+08:00",
)

# 保持临时目录引用存活（TemporaryDirectory 对象被 GC 会删除目录）
_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _migrated_db() -> str:
    td = tempfile.TemporaryDirectory()
    _TMP_DIRS.append(td)  # 防止 GC 提前删除
    db = os.path.join(td.name, "stock_data.db")
    run_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_account VALUES (?,?,?,?,?,?)", _ACCOUNT_ROW
    )
    conn.commit()
    conn.close()
    return db


def test_invalid_order_state_rejected():
    """非法订单状态被 CHECK 拒绝。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
            " VALUES ('o1','k1',1,'SCAN','000001.SZ','BUY',100,'BOGUS_STATE',0,"
            " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 非法订单状态被拒")


def test_valid_order_states_accepted():
    """全部合法状态可插入（状态机验收）。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    states = ["DRAFT", "CONFIRMED", "QUEUED", "FILLED", "PARTIALLY_FILLED_EXPIRED",
              "EXPIRED", "REJECTED"]
    for i, st in enumerate(states):
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
            " VALUES (?,?,1,'SCAN','000001.SZ','BUY',100,?,0,"
            " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')",
            (f"o{i}", f"k{i}", st),
        )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM pt_order").fetchone()[0]
    conn.close()
    assert n == len(states)
    print(f"[PASS] {len(states)} 个合法状态全部可插入")


def test_float_amount_rejected():
    """浮点金额被 CHECK(amount_fen = CAST(amount_fen AS INTEGER)) 拒绝。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
            " ref_id, occurred_at) VALUES (1,'INITIAL',1.5,1000000,NULL,"
            " '2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 浮点金额被拒")


def test_negative_balance_rejected():
    """负现金余额被 CHECK 拒绝（不允许负现金）。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
            " ref_id, occurred_at) VALUES (1,'BUY',-500,-100, NULL,"
            " '2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 负现金余额被拒")


def test_unknown_instrument_type_rejected():
    """未知 instrument 类型被 CHECK 拒绝。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO instrument_rules (ts_code, inst_type, updated_at)"
            " VALUES ('000001.SZ','FUTURE','2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 未知 instrument 类型被拒")


def test_invalid_rule_values_rejected():
    """非法规则值（负佣金/零手数）被 CHECK 拒绝。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO instrument_rules (ts_code, inst_type, commission_bps,"
            " min_commission_fen, sell_tax_bps, other_fee_bps, slippage_bps,"
            " lot_size, updated_at) VALUES ('000001.SZ','STOCK',-1,500,10,1,10,100,"
            " '2026-08-07T10:00:00+08:00')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO instrument_rules (ts_code, inst_type, commission_bps,"
            " min_commission_fen, sell_tax_bps, other_fee_bps, slippage_bps,"
            " lot_size, updated_at) VALUES ('000002.SZ','STOCK',5,500,10,1,10,0,"
            " '2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 非法规则值被拒")


def test_order_idempotency_key_unique():
    """幂等键 UNIQUE：同 key 二次插入被拒（并发预留单次成功的前提）。"""
    db = _migrated_db()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
        " ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
        " VALUES ('o1','same_key',1,'SCAN','000001.SZ','BUY',100,'DRAFT',0,"
        " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
            " VALUES ('o2','same_key',1,'SCAN','000001.SZ','BUY',100,'DRAFT',0,"
            " '2026-08-07T10:00:00+08:00','2026-08-07T10:00:00+08:00')"
        )
    conn.close()
    print("[PASS] 幂等键唯一约束生效")
