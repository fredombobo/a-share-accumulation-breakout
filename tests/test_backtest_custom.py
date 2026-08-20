"""backtest_custom.py 离线单测：窗口解析 / 报告组装 / 门禁 / 试验历史。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest_custom as bc


def test_parse_window_valid():
    assert bc.parse_window("20230801:20250731") == ("20230801", "20250731")


def test_parse_window_invalid():
    with pytest.raises(SystemExit):
        bc.parse_window("20230801")
    with pytest.raises(SystemExit):
        bc.parse_window("20230801:20230801")
    with pytest.raises(SystemExit):
        bc.parse_window("abc:def")


def test_report_id_stable_and_sensitive():
    a = {"strategy": "A", "exit": {"stop_pct": 0.07}, "signal": None}
    b = {"strategy": "A", "exit": {"stop_pct": 0.08}, "signal": None}
    assert bc.report_id(a) == bc.report_id(a)
    assert bc.report_id(a) != bc.report_id(b)
    # 形态阈值变化也必须改变 report_id
    c = {"strategy": "A", "exit": {"stop_pct": 0.07}, "signal": {"box_max_amp": 0.3}}
    assert bc.report_id(a) != bc.report_id(c)


def test_history_append_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "HISTORY_PATH", tmp_path / "history.jsonl")
    bc.append_history({"report_id": "x"})
    bc.append_history({"report_id": "y"})
    rows = bc.load_history()
    assert [r["report_id"] for r in rows] == ["x", "y"]
    # 损坏行应被跳过而非崩溃
    (tmp_path / "history.jsonl").open("a", encoding="utf-8").write("not-json\n")
    assert len(bc.load_history()) == 2


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_run_backtest_flow(monkeypatch, tmp_path):
    """端到端（打桩回测引擎）：验证报告结构、门禁计算与历史登记。"""
    class FakePlan:
        mode: ClassVar[str] = "full"
        is_start: ClassVar[str] = "20230801"
        is_end: ClassVar[str] = "20250731"
        oos_start: ClassVar[str] = "20250801"
        oos_end: ClassVar[str] = "20260731"
        n_dates: ClassVar[int] = 972
        wf_windows: ClassVar[list] = [
            ("20230801", "20240731", "20240801", "20250131"),
            ("20240201", "20250131", "20250201", "20250731"),
            ("20240801", "20250731", "20250801", "20260131"),
        ]
        can_claim_edge: ClassVar[bool] = True

    monkeypatch.setattr(bc, "ROOT", tmp_path)
    monkeypatch.setattr(bc, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(bc, "recommend_research_plan", lambda: FakePlan())

    base_row = {
        "n_trades": 50, "net_n_trades": 45, "net_win_rate": 0.40,
        "net_profit_factor": 1.5, "net_max_drawdown": 0.10,
        "net_avg_return": 0.01, "commission": 10.0, "stamp_tax": 1.0,
        "slippage_cost": 2.0, "net_unfilled": 5, "win_rate": 0.42,
        "profit_factor": 1.6,
    }

    def fake_run_is_oos(**kwargs):
        oos_row = {f"oos_{k}": v for k, v in base_row.items()}
        return {"is": _df([base_row]), "oos": _df([oos_row]), "msg": None, "mode": "single"}

    monkeypatch.setattr(bc, "run_is_oos", fake_run_is_oos)

    class FakeWfRow(dict):
        def to_dict(self):
            return dict(self)

    class FakeWf:
        empty = False

        class _Idx:
            def __getitem__(self, _i):
                return FakeWfRow(
                    wf_pass=True, train_mean_pf=1.5, oos_mean_pf=1.3, wf_detail=[]
                )

        iloc = _Idx()

    monkeypatch.setattr(bc, "wf_recheck", lambda *a, **k: FakeWf())

    class FakeStore:
        def max_trade_date(self, _table):
            return "20260812"

    monkeypatch.setattr("local_store.LocalStore", lambda: FakeStore())

    args = SimpleNamespace(
        strategy="A", vol_ratio_min=1.6, stop_pct=0.07, exit_window=10, strong_reset=3,
        box_min_days=None, box_max_days=None, box_max_amp=None,
        breakout_vol_ratio=None, breakout_chg_min=None, breakout_chg_max=None,
        breakout_window_days=None, box_max_mid_drawdown=None,
        pos_trend_max_drop=None, breakout_vs_recent_vol_ratio=None,
        require_structure=None,
        max_codes=600, step=10,
    )
    setattr(args, "is", None)
    args.oos = None

    report = bc.run_backtest(args)

    # 报告结构
    assert report["windows"]["mode"] == "full"
    assert report["data"]["max_trade_date"] == "20260812"
    assert report["hold_ratio"]["pf"] == 1.0  # oos_pf / is_pf = 1.5/1.5
    # IS 视图必须带 is_ 前缀、OOS 视图带 oos_ 前缀（展示层契约）
    assert report["is"]["is_net_n_trades"] == 45
    assert report["oos"]["oos_net_n_trades"] == 45
    assert report["is"]["is_net_profit_factor"] == 1.5
    # 门禁计算
    gates = {g["name"]: g for g in report["gates"]}
    assert gates["OOS 净交易数"]["pass"] is True      # 45 ≥ 30
    assert gates["OOS 净胜率"]["pass"] is True         # 0.40 ≥ 0.30
    assert gates["OOS 净最大回撤"]["pass"] is True     # 0.10 ≤ 0.25
    assert gates["OOS/IS PF 保持率"]["pass"] is True   # 1.0 ≥ 0.8
    assert gates["WF 三窗复核"]["pass"] is True
    # 披露与历史
    assert any("幸存者偏差" in d for d in report["disclosures"])
    assert any("多重比较" in d for d in report["disclosures"])
    history = bc.load_history()
    assert len(history) == 1
    assert history[0]["report_id"] == report["report_id"]
    # 输出文件
    assert (tmp_path / "runtime" / f"custom_bt_{report['report_id']}.json").is_file()
    md = (tmp_path / "runtime" / f"custom_bt_{report['report_id']}.md").read_text(encoding="utf-8")
    assert "净盈亏比 PF" in md


def test_run_backtest_rejects_insufficient(monkeypatch):
    class InsufficientPlan:
        mode: ClassVar[str] = "insufficient"
        n_dates: ClassVar[int] = 100
        notes: ClassVar[list] = ["数据不足"]

    monkeypatch.setattr(bc, "recommend_research_plan", lambda: InsufficientPlan())
    args = SimpleNamespace()
    with pytest.raises(SystemExit):
        bc.run_backtest(args)
