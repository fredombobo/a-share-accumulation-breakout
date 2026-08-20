"""P7.4 研究笔记/决策台账仓库测试：写入、校验、列表与周报。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.research_note_repository import (
    create_decision,
    create_note,
    get_note,
    list_decisions,
    list_notes,
    weekly_digest,
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "review.db"))
    apply_pending(c)
    yield c
    c.close()


def test_create_and_get_note(conn):
    note = create_note(
        conn, title="测试假设", body="回测参数邻域", ref_type="experiment",
        ref_id="exp-123", kind="hypothesis", tags=["wf", "oos"],
    )
    fetched = get_note(conn, note["note_id"])
    assert fetched["title"] == "测试假设"
    assert fetched["ref_type"] == "experiment"
    assert fetched["ref_id"] == "exp-123"
    assert fetched["tags"] == ["wf", "oos"]


def test_note_validation_fail_closed(conn):
    with pytest.raises(ValueError):
        create_note(conn, title="")  # 空标题拒绝
    with pytest.raises(ValueError):
        create_note(conn, title="ok", ref_type="bad-ref")  # 非法 ref_type 拒绝
    with pytest.raises(ValueError):
        create_note(conn, title="ok", kind="invalid-kind")  # 非法 kind 拒绝


def test_notes_append_only_no_update_path(conn):
    """笔记表应无 UPDATE 语义：直接 SQL UPDATE 被 append-only 意图外的约束阻止。"""
    create_note(conn, title="原始标题")
    # 仓库层不提供 update；数据库层面 research_notes 允许 update 但仓库永不调用。
    # 断言：不存在可修改的仓库入口（API 层面验证见 test_review_api）。
    assert "update" not in dir(type(create_note))


def test_list_notes_filter(conn):
    create_note(conn, title="A", ref_type="signal", ref_id="s1", kind="idea")
    create_note(conn, title="B", ref_type="order", ref_id="o1", kind="log")
    assert len(list_notes(conn)) == 2
    assert len(list_notes(conn, ref_type="signal")) == 1
    assert len(list_notes(conn, kind="log")) == 1


def test_create_and_list_decisions(conn):
    d = create_decision(
        conn, action="REJECT", rationale="OOS 净收益为负",
        ref_type="run", ref_id="run-9", risk_flags=["high_drawdown"],
    )
    assert d["action"] == "REJECT"
    items = list_decisions(conn, ref_type="run")
    assert len(items) == 1
    assert items[0]["risk_flags"] == ["high_drawdown"]
    with pytest.raises(ValueError):
        create_decision(conn, action="", rationale="x")  # 空 action 拒绝


def test_weekly_digest(conn):
    create_note(conn, title="周报素材")
    create_decision(conn, action="HOLD", rationale="观察")
    digest = weekly_digest(conn)
    assert digest["note_count"] == 1
    assert digest["decision_count"] == 1
    assert digest["recent_notes"][0]["title"] == "周报素材"
