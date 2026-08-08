"""阶段2 验收：账户初始化 + 期初持仓导入。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from paper_trading.account import (
    commit_import,
    create_account,
    get_account,
    opening_equity,
    parse_portfolio_json,
    preview_import,
    validate_import_item,
)
from paper_trading.errors import DomainError

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _setup() -> tuple[str, str]:
    """返回 (db_path, portfolio_path)，含预置日线（000001.SZ 10元 / 600001.SH 5元）。"""
    td = tempfile.TemporaryDirectory()
    _TMP_DIRS.append(td)
    db = os.path.join(td.name, "stock_data.db")
    # 用 LocalStore 建 daily 等原表（_init_schema）+ 自动迁移领域表
    from local_store import LocalStore

    LocalStore(db_path=db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT OR IGNORE INTO daily (ts_code, trade_date, open, high, low, close,"
        " vol, amount) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("000001.SZ", "20260805", 9.9, 10.1, 9.8, 10.0, 1000.0, 10000.0),
            ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 1200.0, 12200.0),
            ("600001.SH", "20260805", 4.9, 5.1, 4.8, 5.0, 2000.0, 10000.0),
            ("600001.SH", "20260806", 5.0, 5.2, 4.9, 5.1, 2100.0, 10600.0),
        ],
    )
    conn.commit()
    conn.close()

    pf = os.path.join(td.name, "portfolio.json")
    return db, pf


def _write_portfolio(pf: str, positions: list[dict]) -> None:
    Path(pf).write_text(
        json.dumps({"updated_at": "2026-08-03T23:01:18", "positions": positions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_create_account_single_and_duplicate():
    """创建账户：唯一账户 + 初始现金流水 + 重复创建报冲突。"""
    db, _ = _setup()
    acct = create_account(db, 1_000_000)
    assert acct["account_id"] == 1
    assert acct["initial_cash_fen"] == 1_000_000
    assert acct["cash_fen"] == 1_000_000
    assert acct["status"] == "ACTIVE"
    # 初始现金流水
    conn = sqlite3.connect(db)
    flow = conn.execute("SELECT kind, amount_fen, balance_fen FROM pt_cash_flow").fetchall()
    conn.close()
    assert flow == [("INITIAL", 1_000_000, 1_000_000)]
    # 重复创建
    with pytest.raises(DomainError) as ei:
        create_account(db, 500_000)
    assert ei.value.code == "ACCOUNT_ALREADY_EXISTS"
    print("[PASS] 账户创建 + 初始现金流水 + 重复创建拒绝")


def test_get_account_not_found():
    """无账户时读取 → ERR_UNKNOWN_ACCOUNT。"""
    db, _ = _setup()
    with pytest.raises(DomainError) as ei:
        get_account(db)
    assert ei.value.code == "ACCOUNT_NOT_FOUND"
    print("[PASS] 未创建账户读取报错")


def test_validate_import_items_errors_listed():
    """校验：非整数数量/负数/未知代码/缺失成本逐条列出，不静默修正。"""
    items = [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 100},          # valid
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 100.5},        # 非整数
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": -100},         # 负数
        {"ts_code": "999999.XX", "cost": 10.0, "shares": 100},          # 未知代码
        {"ts_code": "000001.SZ", "cost": None, "shares": 100},          # 缺失成本
    ]
    known = {"000001.SZ", "600001.SH"}
    results = [validate_import_item(it, known_codes=known) for it in items]
    assert results[0]["valid"] is True
    assert results[0]["errors"] == []
    assert results[1]["valid"] is False and any("非整数" in e for e in results[1]["errors"])
    assert results[2]["valid"] is False and any("正整数" in e for e in results[2]["errors"])
    assert results[3]["valid"] is False and any("未知代码" in e for e in results[3]["errors"])
    assert results[4]["valid"] is False and any("成本缺失" in e for e in results[4]["errors"])
    print("[PASS] 非法项逐条列出错误（4 类不静默修正）")


def test_preview_import_shows_validation_and_quotes():
    """预览：展示校验错误 + 当前行情 + valid/invalid 计数。"""
    db, pf = _setup()
    _write_portfolio(pf, [
        {"ts_code": "000001.SZ", "name": "平安银行", "cost": 10.0, "shares": 200,
         "stop_loss": 9.0, "opened_at": "2026-08-01T10:00:00"},
        {"ts_code": "999999.XX", "cost": 5.0, "shares": 100},  # 未知代码
    ])
    pv = preview_import(db, pf)
    assert pv["total"] == 2
    assert pv["valid_count"] == 1
    assert pv["invalid_count"] == 1
    assert pv["has_invalid"] is True
    good = pv["items"][0]
    assert good["ts_code"] == "000001.SZ"
    assert good["last_close"] == 10.2  # 最近收盘（20260806）
    assert good["last_date"] == "20260806"
    bad = pv["items"][1]
    assert bad["valid"] is False and bad["errors"]
    print("[PASS] 预览含校验错误 + 当前行情 + 计数")


def test_commit_import_creates_opening_lots_not_debit_cash():
    """确认导入：生成 OPENING 批次，不倒扣初始化现金。"""
    db, pf = _setup()
    _write_portfolio(pf, [
        {"ts_code": "000001.SZ", "name": "平安银行", "cost": 10.0, "shares": 200,
         "stop_loss": 9.0, "opened_at": "2026-08-01T10:00:00"},
        {"ts_code": "600001.SH", "name": "上汽", "cost": 5.0, "shares": 100,
         "opened_at": "2026-08-07T09:00:00"},  # 当日建仓 → T+1
    ])
    create_account(db, 1_000_000)
    r = commit_import(db, pf, as_of_date="20260806")
    assert r["imported"] == 2
    conn = sqlite3.connect(db)
    lots = conn.execute(
        "SELECT ts_code, remaining_qty, cost_price_micro, sellable_date FROM pt_position_lot"
    ).fetchall()
    conn.close()
    lots_by_code = {l[0]: l for l in lots}
    # 000001 期初仓（8/1 早于 8/6）→ 立即卖
    assert lots_by_code["000001.SZ"][1:] == (200, 10_000_000, "20260806")
    # 600001 当日建仓（8/7 晚于 as_of）→ T+1 下一交易日（8/7 周五 → 8/10 周一）
    assert lots_by_code["600001.SH"][1] == 100
    assert lots_by_code["600001.SH"][2] == 5_000_000
    assert lots_by_code["600001.SH"][3] in ("20260807", "20260810")
    # 现金未扣（不倒扣）
    acct = get_account(db)
    assert acct["cash_fen"] == 1_000_000
    print("[PASS] OPENING 批次生成 + 不倒扣现金 + 可卖日规则")


def test_commit_import_idempotent_same_hash():
    """同哈希重复导入：不重复增加持仓。"""
    db, pf = _setup()
    _write_portfolio(pf, [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 200,
         "opened_at": "2026-08-01T10:00:00"},
    ])
    create_account(db, 1_000_000)
    r1 = commit_import(db, pf, as_of_date="20260806")
    assert r1["imported"] == 1
    r2 = commit_import(db, pf, as_of_date="20260806")
    assert r2["imported"] == 0 and r2["skipped_existing"] is True
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM pt_position_lot").fetchone()[0]
    conn.close()
    assert n == 1, "重复导入不得增加持仓"
    print("[PASS] 同哈希重复导入幂等（持仓数不变）")


def test_opening_equity_cash_plus_market_value():
    """期初权益 = 初始化现金 + 期初持仓收盘市值。"""
    db, pf = _setup()
    _write_portfolio(pf, [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 200, "opened_at": "2026-08-01T10:00:00"},
        {"ts_code": "600001.SH", "cost": 5.0, "shares": 100, "opened_at": "2026-08-01T10:00:00"},
    ])
    create_account(db, 1_000_000)  # 10000 元
    commit_import(db, pf, as_of_date="20260806")
    eq = opening_equity(db)
    # 000001: 200股×10.2元=2040元; 600001: 100股×5.1元=510元 → 市值 2550 元 = 255000 分
    assert eq["market_value_fen"] == 255_000, eq
    assert eq["total_equity_fen"] == 1_000_000 + 255_000, eq
    print(f"[PASS] 期初权益 = 现金 + 市值 = {eq['total_equity_fen']} 分")


def test_parse_portfolio_missing_file():
    """文件不存在 → 领域错误。"""
    _db, pf = _setup()
    with pytest.raises(DomainError) as ei:
        parse_portfolio_json(os.path.join(os.path.dirname(pf), "nope.json"))
    assert ei.value.code == "PORTFOLIO_FILE_NOT_FOUND"
    print("[PASS] 文件缺失报错")
