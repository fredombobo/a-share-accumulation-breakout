"""Completed losing and empty trials survive a later research-stage failure."""
from types import SimpleNamespace

import pandas as pd
import pytest

from ab_screener.research.professional_runner import execute_professional_run


def test_checkpoint_precedes_later_failure_and_keeps_all_trials(monkeypatch):
    import optimizer
    from ab_screener.research import pit_reader

    monkeypatch.setattr(pit_reader, "build_research_pit_snapshot",
                        lambda *args, **kwargs: SimpleNamespace(universe=["000001.SZ"]))

    def fake_grid(**kwargs):
        return pd.DataFrame([{**params, "net_n_trades": index, "portfolio_total_return": -.1,
                              "portfolio_status": "PASS"}
                             for index, params in enumerate(kwargs["combos_override"])])

    monkeypatch.setattr(optimizer, "run_grid", fake_grid)
    saved = []

    def progress(phase, percent, message):
        if phase == "GRID" and message.endswith("完成"):
            raise RuntimeError("later stage failed")

    with pytest.raises(RuntimeError, match="later stage failed"):
        execute_professional_run("no-database.db", {
            "parameters": {}, "universe": {"codes": ["000001.SZ"]}, "sample_step": 10,
            "windows": {"is": ["20240101", "20241231"], "oos": ["20250101", "20251231"]}},
            progress=progress, cancel_check=lambda: False, trial_checkpoint=saved.extend)
    assert len(saved) == 18
    assert len({row["param_id"] for row in saved}) == 18
    assert saved[0]["is"]["net_n_trades"] == 0
    assert all(row["oos"]["net_total_return"] == -.1 for row in saved)
