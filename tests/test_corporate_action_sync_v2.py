"""V2R-D：公司行为 PIT 同步测试。

覆盖（主计划 Task 2 Step 1）：
- revision 切换：同一业务键（ts_code, ex_date, kind）内容修订后，
  as-of 读取在修订 available_at 前后分别返回旧/新版本。
- available_at 晚于 decision_at 不可见（fail-closed，不提前暴露）。
- 无权限显式失败：数据源接口无权限时必须显式抛错，不得静默返回空。
- 重复抓取幂等：同一载荷重复入账返回同一记录，不重复建 revision。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ab_screener.data.adapters.tushare_pit import fetch_corporate_actions
from ab_screener.data.corporate_action_repository import (
    CorporateActionError,
    CorporateActionRepository,
)


def action_fixture(**overrides: object) -> dict[str, object]:
    """公司行为 fixture：默认 000001.SZ 20260710 DIVIDEND 派息 250 分/10股。"""
    base: dict[str, object] = {
        "ts_code": "000001.SZ",
        "ex_date": "20260710",
        "kind": "DIVIDEND",
        "payload": {"cash_div_fen": 250},
        "source": "tushare",
        "available_at": "2026-08-22T18:00:00+08:00",
        "effective_at": "2026-07-10T00:00:00+08:00",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def repo(tmp_path: Path) -> CorporateActionRepository:
    return CorporateActionRepository(tmp_path / "ca_pit.db")


def test_corporate_action_is_not_visible_before_available_at(
    repo: CorporateActionRepository,
) -> None:
    repo.append(action_fixture(available_at="2026-08-22T18:00:00+08:00"))
    rows = repo.list_asof(
        "000001.SZ",
        decision_at="2026-08-22T17:59:59+08:00",
    )
    assert rows == []


def test_revision_switch_returns_latest_visible_version(
    repo: CorporateActionRepository,
) -> None:
    repo.append(action_fixture(
        payload={"cash_div_fen": 250},
        available_at="2026-08-22T18:00:00+08:00",
    ))
    # 更正：派息改为 180 分，新修订在 2026-08-23 09:00 可用
    repo.append(action_fixture(
        payload={"cash_div_fen": 180},
        available_at="2026-08-23T09:00:00+08:00",
    ))
    early = repo.list_asof("000001.SZ", decision_at="2026-08-22T19:00:00+08:00")
    late = repo.list_asof("000001.SZ", decision_at="2026-08-23T10:00:00+08:00")
    assert len(early) == 1 and len(late) == 1
    assert early[0]["revision"] == 1
    assert early[0]["payload"]["cash_div_fen"] == 250
    assert late[0]["revision"] == 2
    assert late[0]["payload"]["cash_div_fen"] == 180


def test_each_record_carries_full_pit_metadata(
    repo: CorporateActionRepository,
) -> None:
    repo.append(action_fixture(available_at="2026-08-22T18:00:00+08:00"))
    rows = repo.list_asof("000001.SZ", decision_at="2026-08-22T19:00:00+08:00")
    assert len(rows) == 1
    record = rows[0]
    for field in ("effective_at", "available_at", "ingested_at", "source", "revision"):
        assert field in record, f"缺少 PIT 字段 {field}"
        assert record[field] not in (None, ""), f"PIT 字段 {field} 为空"


def test_repeated_append_is_idempotent(repo: CorporateActionRepository) -> None:
    first = repo.append(action_fixture())
    second = repo.append(action_fixture())
    assert first == second  # 幂等：返回同一 corporate_action_id
    rows = repo.list_asof("000001.SZ", decision_at="2026-08-23T00:00:00+08:00")
    assert len(rows) == 1
    assert rows[0]["revision"] == 1


class _NoPermissionPro:
    """模拟 tushare 公司行为接口无权限：必须显式失败。"""

    def dividend(self, **kwargs: object) -> None:
        raise RuntimeError("抱歉，您没有访问该接口的权限")


def test_no_permission_fails_explicitly() -> None:
    with pytest.raises(CorporateActionError, match="公司行为"):
        fetch_corporate_actions(_NoPermissionPro(), ts_code="000001.SZ")


class _FakePro:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def dividend(self, **kwargs: object):
        import pandas as pd

        return pd.DataFrame(self._rows)


def test_fetch_corporate_actions_maps_rows() -> None:
    pro = _FakePro([{
        "ts_code": "000001.SZ", "ann_date": "20260601", "div_proc": "实施",
        "stk_div": 0.0, "cash_div_tax": 2.5, "record_date": "20260709",
        "ex_date": "20260710",
    }])
    rows = fetch_corporate_actions(pro, ts_code="000001.SZ")
    assert len(rows) == 1
    assert rows[0]["ex_date"] == "20260710"
    assert rows[0]["kind"] == "DIVIDEND"
    assert rows[0]["source"] == "tushare"
