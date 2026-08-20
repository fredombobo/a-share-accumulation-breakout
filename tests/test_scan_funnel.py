"""P4.2 测试：扫描漏斗（显式阶段、异常隔离、集合守恒）+ 信号管线落库。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.application.scan_funnel import (
    FunnelError,
    ScanFunnel,
    assert_set_conservation,
)
from ab_screener.application.signal_pipeline import run_signal_pipeline
from ab_screener.data.migration_registry import apply_pending


def test_funnel_explicit_stages_not_hardcoded():
    """阶段列表由调用方显式声明（禁止写死阶段数量）。"""
    funnel = ScanFunnel(["load", "filter", "score"])
    assert funnel.stages == ["load", "filter", "score"]
    with pytest.raises(FunnelError, match="至少需要一个阶段"):
        ScanFunnel([])
    with pytest.raises(FunnelError, match="重复"):
        ScanFunnel(["a", "a"])


def test_funnel_runs_all_stages_with_isolation():
    funnel = ScanFunnel(["a", "b", "c"])

    def stage_a(value, stage):
        return value + ["A"]

    def stage_b(value, stage):
        raise RuntimeError("b 阶段崩溃")

    def stage_c(value, stage):
        return value + ["C"]

    results = funnel.run([], {"a": stage_a, "b": stage_b, "c": stage_c})
    assert results["a"].ok and results["a"].output == ["A"]
    assert not results["b"].ok and "崩溃" in results["b"].error
    # c 阶段仍执行（异常隔离），但输入链断裂
    assert results["c"].ok
    # 缺阶段实现 → 抛错
    with pytest.raises(FunnelError, match="缺少阶段实现"):
        funnel.run([], {"a": stage_a})


def test_set_conservation_ab_branches():
    inputs = {"000001.SZ", "600000.SH", "300750.SZ"}
    branch_a = {"000001.SZ", "600000.SH"}
    branch_b = {"300750.SZ"}
    assert_set_conservation(inputs, [branch_a, branch_b])  # 守恒
    with pytest.raises(FunnelError, match="集合不守恒"):
        assert_set_conservation(inputs, [{"000001.SZ"}])  # 缺 2 只


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "pipe.db"))
    apply_pending(c)
    yield c
    c.close()


def _bars() -> pd.DataFrame:
    rng = pd.date_range("2026-01-01", periods=100, freq="B")
    return pd.DataFrame(
        {
            "date": rng.strftime("%Y%m%d"),
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2,
            "vol": 100_000, "amount": 1e8,
        }
    )


def test_signal_pipeline_saves_observations_idempotent(conn):
    result = run_signal_pipeline(
        conn, bars=_bars(), ts_code="000001.SZ",
        snapshot_id="snap1", input_hash="ih1",
    )
    assert result["plugins_run"]  # 六插件都跑了
    assert "errors" in result
    # 重跑幂等：观察数不变
    second = run_signal_pipeline(
        conn, bars=_bars(), ts_code="000001.SZ",
        snapshot_id="snap1", input_hash="ih1",
    )
    assert second["saved_count"] == 0  # 全部幂等跳过
    n = conn.execute("SELECT COUNT(*) FROM signal_observations").fetchone()[0]
    assert n == result["saved_count"]
