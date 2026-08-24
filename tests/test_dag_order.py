"""固定 EOD DAG 顺序与依赖边（§4 冻结合同）：9 步显式依赖，不靠列表位置。"""
from __future__ import annotations

import pytest

from ab_screener.operations.dag import (
    DAG_STEPS,
    DEPENDENCY_EDGES,
    DagError,
    build_eod_dag,
)


def test_contract_steps_exactly_nine_business_steps():
    assert DAG_STEPS == (
        "eod_gates",                 # 1 交易日/数据新鲜度/公司行为门禁
        "release_matured_lots",      # 2 释放到期可卖批次
        "match_confirmed_orders",    # 3 撮合此前确认订单
        "close_valuation",           # 4 收盘估值
        "risk_pnl_snapshot",         # 5 风险与损益快照
        "internal_reconciliation",   # 6 内部对账
        "outcome_backfill",          # 7 信号 outcome 回填
        "generate_drafts",           # 8 读取当日信号生成下一交易日草稿
        "daily_manifest",            # 9 固化 daily manifest
    )


def test_contract_dependency_edges_explicit_and_linear():
    """每个步骤有显式依赖边；顺序为线性因果链。"""
    assert tuple(DEPENDENCY_EDGES) == DAG_STEPS
    prev = None
    for step in DAG_STEPS:
        if prev is None:
            assert DEPENDENCY_EDGES[step] == ()
        else:
            assert DEPENDENCY_EDGES[step] == (prev,), f"{step} 依赖 {DEPENDENCY_EDGES[step]}"
        prev = step


def test_contract_dependency_set_matches_step_set():
    assert set(DAG_STEPS) == set(DEPENDENCY_EDGES)


def test_build_eod_dag_matches_contract(tmp_path):
    """生产 EOD factory 接线结果必须通过合同校验（顺序 + 每条依赖边）。"""
    dag = build_eod_dag(str(tmp_path / "no-db-yet.db"))
    assert dag.order() == list(DAG_STEPS)
    dag.validate_contract()


def test_validate_contract_rejects_wrong_order(tmp_path):
    from ab_screener.operations.dag import DailyDag, StepSpec

    def fn(**kwargs):
        return {}

    bad = DailyDag([
        StepSpec("daily_manifest", "GLOBAL", "all", fn, ()),
        StepSpec("eod_gates", "GLOBAL", "all", fn, ("daily_manifest",)),
    ])
    with pytest.raises(DagError, match="合同"):
        bad.validate_contract()
