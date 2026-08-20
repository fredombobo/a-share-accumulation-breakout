from __future__ import annotations

import pandas as pd
import pytest

import optimizer
import walkforward


class _PendingFuture:
    pass


class _FakePool:
    pass


def test_collect_pool_results_abandons_workers_when_cancelled(monkeypatch) -> None:
    abandoned: list[object] = []
    monkeypatch.setattr("scan_runtime.abandon_pool", lambda pool: abandoned.append(pool))
    pool = _FakePool()

    with pytest.raises(optimizer.ResearchCancelled):
        optimizer._collect_pool_results(
            pool,
            {_PendingFuture()},
            chunk_count=1,
            progress_cb=None,
            cancel_check=lambda: True,
        )

    assert abandoned == [pool]


def test_is_oos_forwards_persisted_cancel_check(monkeypatch) -> None:
    seen: list[object] = []

    def fake_grid(**kwargs):
        seen.append(kwargs.get("cancel_check"))
        return pd.DataFrame()

    monkeypatch.setattr(walkforward, "run_grid", fake_grid)
    token = lambda: False

    walkforward.run_is_oos(strategy="A", cancel_check=token)

    assert seen == [token]


def test_wf_recheck_forwards_persisted_cancel_check(monkeypatch) -> None:
    seen: list[object] = []

    def fake_eval(*_args, **kwargs):
        seen.append(kwargs.get("cancel_check"))
        return {"net_profit_factor": 1.1, "net_max_drawdown": 0.2,
                "net_win_rate": 0.4, "net_n_trades": 35}

    monkeypatch.setattr(walkforward, "eval_combo", fake_eval)
    token = lambda: False
    combo = {"strategy": "A", "vol_ratio_min": 1.5, "strong_reset": 3,
             "exit_window": 10, "stop_pct": 0.07}

    walkforward.wf_recheck(
        [combo],
        windows=[("20230101", "20230630", "20230701", "20231231")],
        cancel_check=token,
    )

    assert seen == [token, token]
