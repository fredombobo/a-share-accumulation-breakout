from __future__ import annotations

import pandas as pd

import optimizer
from ab_screener.research.portfolio_accounting import PortfolioPolicy


def _market() -> pd.DataFrame:
    rows: list[dict] = []
    prices = {
        "000001.SZ": [10, 11, 12, 14],
        "000002.SZ": [20, 21, 20, 18],
    }
    for code, values in prices.items():
        for date, price in zip(
            ("20260801", "20260802", "20260803", "20260804"),
            values,
        ):
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price + 0.5,
                    "pre_close": price - 0.5,
                    "vol": 100_000,
                    "amount": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def _trade(code: str, *, net_return: float, exit_price: float) -> dict:
    return {
        "ts_code": code,
        "date": "20260801",
        "entry_date": "20260802",
        "exit_date": "20260804",
        "exit": "time",
        "exit_price": exit_price,
        "ret": net_return,
        "win": net_return > 0,
        "max_dd": abs(min(net_return, 0)),
        "cost": {
            "filled": True,
            "net_return": net_return,
            "net_pnl": net_return * 100_000,
            "commission": 5.0,
            "stamp_tax": 1.0,
            "other_fee": 1.0,
            "slippage_cost": 2.0,
        },
    }


def test_grid_promotes_portfolio_metrics_and_keeps_trade_diagnostics(monkeypatch) -> None:
    market = _market()
    combo = {
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "stop_pct": 0.07,
    }

    class FakeStore:
        def distinct_dates(self, _table: str) -> list[str]:
            return sorted(market["trade_date"].unique().tolist())

        def load_daily(self, **_kwargs) -> pd.DataFrame:
            return market.copy()

    def fake_worker(payload: tuple) -> dict[str, list[dict]]:
        received_combo = payload[7][0]
        pid = optimizer.param_id(received_combo["strategy"], received_combo)
        return {
            pid: [
                _trade("000001.SZ", net_return=0.50, exit_price=14),
                _trade("000002.SZ", net_return=-0.50, exit_price=18),
            ]
        }

    monkeypatch.setattr("local_store.LocalStore", FakeStore)
    monkeypatch.setattr("parallel_scan.resolve_workers", lambda _workers: 1)
    monkeypatch.setattr(
        optimizer, "research_universe", lambda *_args, **_kwargs: list(market["ts_code"].unique())
    )
    monkeypatch.setattr(optimizer, "_worker_chunk", fake_worker)
    monkeypatch.setattr(optimizer, "BT_MIN_TRADES", 1)

    result = optimizer.run_grid(
        start="20260801",
        end="20260804",
        strategy="A",
        step=1,
        grid={key: [value] for key, value in combo.items() if key != "strategy"},
        portfolio_policy=PortfolioPolicy(),
    )

    row = result.iloc[0]
    assert row["portfolio_status"] == "PASS"
    assert row["portfolio_model_version"] == "research-portfolio-v2.0.0"
    assert row["trade_net_max_drawdown"] == 0.5
    assert row["net_max_drawdown"] < row["trade_net_max_drawdown"]
    assert row["net_avg_return"] == row["net_total_return"]
