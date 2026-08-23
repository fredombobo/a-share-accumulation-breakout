"""V2R-S fill→ENTERED 集成测试：正数量 fill 才进入 ENTERED。

覆盖启动包第一组 RED 测试：
3. CONFIRMED/QUEUED 不进入 ENTERED；正数量 fill 才进入；零成交、拒绝、过期不进入。

使用统一执行核心（v2 FillV2/compute_fill）与信号生命周期仓库，全部 tmp_path 临时库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.signal_repository import (
    append_event,
    projection_status,
    save_observation,
)
from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import FillV2, Quote
from ab_screener.domain.signal_lifecycle import (
    SignalLifecycleError,
    fill_qualifies_for_entered,
)
from ab_screener.strategies.contracts import SignalObservation


def _obs(**over) -> SignalObservation:
    base = {
        "strategy_definition_id": "accumulation_breakout_v1",
        "strategy_hash": "sh1", "input_hash": "ih1", "snapshot_id": "snap1",
        "ts_code": "000001.SZ", "signal_date": "20260810", "config_hash": "ch1",
        "payload": {"box_days": 76}, "explanation": "放量突破",
        "tradeable": True, "entry_definition_id": "NEXT_TRADABLE_OPEN_EXECUTION_V1",
    }
    base.update(over)
    return SignalObservation(**base)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "fill.db"))
    apply_pending(c)
    yield c
    c.close()


def _save(conn) -> str:
    return save_observation(conn, _obs())


def _advance_to_order_created(conn, oid: str) -> None:
    for event in ("QUALIFIED", "TRADEABLE", "ORDER_CREATED"):
        append_event(conn, observation_id=oid, event_type=event, actor="system")


def _quote(price_micro: int = 10_000_000, vol: int = 1_000_000) -> Quote:
    return Quote(
        ts_code="000001.SZ", trade_date="20260811",
        open_micro=price_micro, high_micro=price_micro, low_micro=price_micro,
        close_micro=price_micro, vol=vol,
        amount_fen=price_micro * vol // 10_000,
    )


def _fill_request(requested_qty: int | None = 1000,
                  cash_available_fen: int | None = 1_000_000_00) -> FillRequest:
    return FillRequest(
        ts_code="000001.SZ", side="BUY", trade_date="20260811",
        input_hash="ih", requested_qty=requested_qty,
        cash_available_fen=cash_available_fen,
    )


# ── 领域规则：fill 资格 ──


def test_fill_qualifies_for_entered_domain_rule():
    assert fill_qualifies_for_entered(filled=True, qty=100) is True
    assert fill_qualifies_for_entered(filled=True, qty=0) is False
    assert fill_qualifies_for_entered(filled=False, qty=100) is False
    assert fill_qualifies_for_entered(filled=False, qty=0) is False


# ── 正数量 fill → ENTERED ──


def test_positive_fill_enters_entered(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    fill = compute_fill(_quote(), _fill_request())
    assert fill.filled and fill.qty > 0

    result = apply_fill_to_signal(conn, observation_id=oid, fill=fill)
    assert result["entered"] is True
    assert projection_status(conn, oid)["status"] == "ENTERED"
    # 事件落库：actor=fill
    events = conn.execute(
        "SELECT event_type, actor FROM signal_events WHERE observation_id=?", (oid,)
    ).fetchall()
    assert events[-1] == ("ENTERED", "fill")


# ── 零成交 / 拒绝 / 过期 / 确认中：不进入 ENTERED ──


def test_zero_fill_does_not_enter_entered(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    zero_fill = compute_fill(_quote(vol=0), _fill_request())  # NO_VOLUME → 零成交
    assert zero_fill.filled is False and zero_fill.qty == 0

    result = apply_fill_to_signal(conn, observation_id=oid, fill=zero_fill)
    assert result["entered"] is False
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"
    assert "NO_QUALIFYING_FILL" in result["reason"]


def test_rejected_fill_does_not_enter_entered(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    rejected = compute_fill(_quote(), _fill_request(cash_available_fen=0))  # 无现金 → 拒绝
    assert rejected.filled is False

    result = apply_fill_to_signal(conn, observation_id=oid, fill=rejected)
    assert result["entered"] is False
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"


def test_expired_order_does_not_enter_entered(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    # DAY 余量过期：无 fill，订单状态 EXPIRED → 不得伪装 ENTERED
    result = apply_fill_to_signal(conn, observation_id=oid, fill=None, order_state="EXPIRED")
    assert result["entered"] is False
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"

    # 显式过期的 fill 对象同样被拒绝
    expired_fill = FillV2(
        ts_code="000001.SZ", side="BUY", trade_date="20260811",
        filled=False, qty=0, price_micro=0, notional_fen=0,
        fees=FillV2_fees(), cash_delta_fen=0, reason="DAY_REMAINDER_EXPIRED",
        participation_bps=500, max_qty=0, input_hash="ih",
    )
    result2 = apply_fill_to_signal(conn, observation_id=oid, fill=expired_fill)
    assert result2["entered"] is False
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"


def test_confirm_and_queued_order_status_do_not_enter(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    for state in ("CONFIRMED", "QUEUED"):
        result = apply_fill_to_signal(conn, observation_id=oid, fill=None, order_state=state)
        assert result["entered"] is False, state
        assert "ORDER_NOT_FILLED" in result["reason"], state
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"


def test_no_fill_does_not_enter(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)
    _advance_to_order_created(conn, oid)
    result = apply_fill_to_signal(conn, observation_id=oid, fill=None)
    assert result["entered"] is False
    assert projection_status(conn, oid)["status"] == "ORDER_CREATED"


# ── fail-closed：非 ORDER_CREATED 状态收到 fill 直接拒绝 ──


def test_fill_on_non_order_created_fails_closed(conn):
    from ab_screener.application.signal_pipeline import apply_fill_to_signal

    oid = _save(conn)  # OBSERVED，未走到 ORDER_CREATED
    fill = compute_fill(_quote(), _fill_request())
    with pytest.raises(SignalLifecycleError, match="ENTERED"):
        apply_fill_to_signal(conn, observation_id=oid, fill=fill)
    assert projection_status(conn, oid)["status"] == "OBSERVED"


def test_domain_transition_still_guards_entered(conn):
    """状态机本身不允许非 ORDER_CREATED 跳 ENTERED（双重护栏）。"""
    oid = _save(conn)
    with pytest.raises(SignalLifecycleError, match="ENTERED"):
        append_event(conn, observation_id=oid, event_type="ENTERED", actor="fill")


def FillV2_fees():
    """零费用拆解（仅测试辅助）。"""
    from ab_screener.domain.execution.models import FeeBreakdown

    return FeeBreakdown(commission_fen=0, stamp_tax_fen=0, other_fee_fen=0, slippage_fen=0)
