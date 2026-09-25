"""横截面 G2 执行器：收益口径（除权/一字板/退市）、股票池、植入效应可检出、零效应不误报、执行器拒绝条件。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ab_screener.research import cross_section as xs
from ab_screener.research.scorecard import build_registration, evaluate

COST = json.loads((Path(__file__).resolve().parents[1] / "configs/research/cost_model_v1.json").read_text(encoding="utf-8"))


def synthetic(n_codes: int = 60, *, drift: float = 0.0, seed: int = 7, start: str = "2015-01-01", end: str = "2017-12-31"):
    """EP 越高日漂移越大（drift × 排名分位）；其余为噪声。"""
    rng = np.random.default_rng(seed)
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range(start, end)]
    codes = [f"{i:06d}.SZ" for i in range(n_codes)]
    pe = rng.uniform(5, 60, n_codes)
    rank = pd.Series(1 / pe).rank(pct=True).to_numpy()
    mv = rng.uniform(1e5, 1e6, n_codes)
    rows, basic = [], []
    for j, code in enumerate(codes):
        pct = rng.normal(0, 1.2, len(dates)) + drift * (rank[j] - 0.5) * 100
        close = 10 * np.cumprod(1 + pct / 100)
        pre = np.concatenate([[10.0], close[:-1]])
        for i, d in enumerate(dates):
            rows.append((code, d, pre[i], max(pre[i], close[i]) * 1.001, min(pre[i], close[i]) * 0.999,
                         close[i], pre[i], pct[i], 1000.0))
            basic.append((code, d, 1.0 + 0.1 * rng.random(), pe[j], mv[j]))
    daily = pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol"])
    basic_df = pd.DataFrame(basic, columns=["ts_code", "trade_date", "turnover_rate", "pe", "total_mv"])
    listing = pd.DataFrame({"ts_code": codes, "list_date": "20100101", "delist_date": None})
    names = pd.DataFrame({"ts_code": codes, "name": [f"股{i}" for i in range(n_codes)],
                          "start_date": "20100101", "end_date": None})
    return daily, basic_df, listing, names


def tiny_panel(**overrides) -> xs.Panel:
    dates = ["20240102", "20240103", "20240104", "20240105"]
    base = {
        "open": [[10, 10], [10, 10], [10, 10], [10, 10]],
        "high": [[10.5, 10.5], [10.5, 10.5], [10.5, 10.5], [10.5, 10.5]],
        "low": [[9.5, 9.5], [9.5, 9.5], [9.5, 9.5], [9.5, 9.5]],
        "close": [[10, 10], [10, 10], [10, 10], [10, 10]],
        "pre_close": [[10, 10], [10, 10], [10, 10], [10, 10]],
        "pct": [[0, 0], [0, 0], [0, 0], [0, 0]],
    }
    base.update(overrides)
    arrays = {k: np.array(v, dtype=float) for k, v in base.items()}
    zeros = np.zeros_like(arrays["open"])
    return xs.Panel(dates=dates, codes=["000001.SZ", "000002.SZ"], vol=zeros + 100, turnover=zeros + 1,
                    pe=zeros + 10, mv=zeros + 1e5, **arrays)


def test_return_uses_pct_chain_so_ex_rights_gap_is_not_a_loss() -> None:
    # 第 2 天除权：收盘价腰斩但 pct_chg 为 +1%，pre_close 已复权
    panel = tiny_panel(
        close=[[10, 10], [5.05, 10], [5.05, 10], [5.05, 10]],
        pre_close=[[10, 10], [5.0, 10], [5.05, 10], [5.05, 10]],
        open=[[10, 10], [5.0, 10], [5.05, 10], [5.05, 10]],
        pct=[[0, 0], [1, 0], [0, 0], [0, 0]],
    )
    r = xs.period_returns(panel, 0, 3)
    assert r[0] == pytest.approx(0.01)
    assert r[1] == pytest.approx(0.0)


def test_one_word_limit_up_on_entry_is_not_bought() -> None:
    panel = tiny_panel(open=[[11, 10], [11, 10], [11, 10], [11, 10]],
                       high=[[11, 10.5], [11, 10.5], [11, 10.5], [11, 10.5]],
                       low=[[11, 9.5], [11, 9.5], [11, 9.5], [11, 9.5]],
                       close=[[11, 10]] * 4, pct=[[10, 0]] * 4)
    assert np.isnan(xs.period_returns(panel, 0, 2)[0])


def test_one_word_limit_down_on_exit_is_deferred() -> None:
    panel = tiny_panel(
        open=[[10, 10], [10, 10], [9, 10], [8.5, 10]],
        high=[[10.5, 10.5], [10.5, 10.5], [9, 10.5], [8.8, 10.5]],
        low=[[9.5, 9.5], [9.5, 9.5], [9, 9.5], [8.4, 9.5]],
        close=[[10, 10], [10, 10], [9, 10], [8.6, 10]],
        pre_close=[[10, 10], [10, 10], [10, 10], [9, 10]],
        pct=[[0, 0], [0, 0], [-10, 0], [-4.4444, 0]],
    )
    r = xs.period_returns(panel, 0, 2)  # 第 3 天一字跌停，顺延到第 4 天开盘
    assert r[0] == pytest.approx(0.9 * 8.5 / 9 - 1)


def test_delisted_during_holding_uses_last_close() -> None:
    nan = np.nan
    panel = tiny_panel(
        open=[[10, 10], [10, 10], [nan, 10], [nan, 10]],
        close=[[10, 10], [9, 10], [nan, 10], [nan, 10]],
        pre_close=[[10, 10], [10, 10], [nan, 10], [nan, 10]],
        pct=[[0, 0], [-10, 0], [nan, 0], [nan, 0]],
    )
    assert xs.period_returns(panel, 0, 2)[0] == pytest.approx(-0.10)


def test_universe_filters_board_st_seasoning_and_small_caps() -> None:
    daily, basic, listing, names = synthetic(n_codes=10, start="2015-01-01", end="2016-06-30")
    extra = daily[daily["ts_code"] == "000000.SZ"].assign(ts_code="830001.BJ")
    daily = pd.concat([daily, extra])
    basic = pd.concat([basic, basic[basic["ts_code"] == "000000.SZ"].assign(ts_code="830001.BJ", total_mv=9e9)])
    listing = pd.concat([listing, pd.DataFrame({"ts_code": ["830001.BJ"], "list_date": ["20100101"]})])
    listing.loc[listing["ts_code"] == "000001.SZ", "list_date"] = "20160101"  # 未满 250 日
    # 000002.SZ 在 2016-03-01 被 *ST：此前的名称止于 2016-02-29
    names.loc[names["ts_code"] == "000002.SZ", "end_date"] = "20160229"
    names = pd.concat([names, pd.DataFrame({"ts_code": ["000002.SZ"], "name": ["*ST 某"],
                                            "start_date": ["20160301"], "end_date": [None]})])
    panel = xs.build_panel(daily, basic)
    f = panel.dates.index("20160531")
    members = xs.universe_at(panel, f, listing, names)
    chosen = {c for c, m in zip(panel.codes, members, strict=True) if m}
    assert "830001.BJ" not in chosen
    assert "000001.SZ" not in chosen
    assert "000002.SZ" not in chosen  # 形成日当时名称为 *ST
    f_before = panel.dates.index("20160229")
    before = {c for c, m in zip(panel.codes, xs.universe_at(panel, f_before, listing, names), strict=True) if m}
    small = basic[basic["trade_date"] == "20160229"].set_index("ts_code")["total_mv"]
    assert small.idxmin() not in before
    assert len(chosen) < 10


def test_planted_ep_effect_is_detected_and_passes_g2_rules() -> None:
    daily, basic, listing, names = synthetic(drift=0.004)
    panel = xs.build_panel(daily, basic)
    series = xs.monthly_series(panel, xs.PRIMARY_SPECS["H-20260925-ep-value"], listing, names,
                               start="20160101", end="20171231", cost=COST, placebo_reps=50)
    stats = xs.g2_statistics(series)
    assert stats["months"] >= 20
    assert stats["ci_lo"] > 0
    assert stats["gross_mean"] > stats["placebo_gross_p95"]


def test_no_effect_is_not_reported_as_edge() -> None:
    daily, basic, listing, names = synthetic(drift=0.0, seed=11)
    panel = xs.build_panel(daily, basic)
    series = xs.monthly_series(panel, xs.PRIMARY_SPECS["H-20260925-ep-value"], listing, names,
                               start="20160101", end="20171231", cost=COST, placebo_reps=50)
    stats = xs.g2_statistics(series)
    assert not (stats["ci_lo"] > 0 and stats["gross_mean"] > stats["placebo_gross_p95"])


def test_signals_max_and_abnormal_turnover() -> None:
    daily, basic, _listing, _names = synthetic(n_codes=5, start="2015-01-01", end="2016-03-31")
    panel = xs.build_panel(daily, basic)
    f = len(panel.dates) - 1
    max20 = xs.signal_at(panel, f, xs.SignalSpec("max", "low", window=20))
    assert np.allclose(max20, np.nanmax(panel.pct[f - 19: f + 1], axis=0))
    aturn = xs.signal_at(panel, f, xs.SignalSpec("aturn", "low", window=20, long_window=250))
    expected = np.nanmean(panel.turnover[f - 19: f + 1], axis=0) / np.nanmean(panel.turnover[f - 249: f + 1], axis=0)
    assert np.allclose(aturn, expected)


# ── 执行器 ──

def _write_db(path: Path, daily: pd.DataFrame, basic: pd.DataFrame, listing: pd.DataFrame) -> None:
    with sqlite3.connect(path) as conn:
        daily.to_sql("daily", conn, index=False)
        basic.to_sql("daily_basic", conn, index=False)
        listing[["ts_code", "list_date"]].to_sql("stock_basic", conn, index=False)
        pd.DataFrame(columns=["ts_code", "list_date", "delist_date"]).to_sql("delisted_basic", conn, index=False)


def _write_reference(ref: Path, names: pd.DataFrame) -> None:
    ref.mkdir(parents=True)
    names.to_csv(ref / "namechange.csv", index=False)
    sha = hashlib.sha256((ref / "namechange.csv").read_bytes()).hexdigest()
    (ref / "namechange.meta.json").write_text(json.dumps({"sha256": sha}), encoding="utf-8")


@pytest.fixture()
def runner(tmp_path: Path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_cross_section_g2

    daily, basic, listing, names = synthetic(n_codes=30, drift=0.004, start="2015-01-01", end="2016-12-31")
    db = tmp_path / "copy.db"
    _write_db(db, daily, basic, listing)
    _write_reference(tmp_path / "ref", names)
    base = ["--hypothesis", "H-20260925-ep-value", "--db", str(db), "--root", str(tmp_path / "ev"),
            "--reference", str(tmp_path / "ref")]
    return run_cross_section_g2, base, tmp_path


def test_runner_refuses_without_registration(runner, capsys) -> None:
    mod, base, _ = runner
    assert mod.main(base) == 2
    assert "未预登记" in capsys.readouterr().out


def test_runner_refuses_without_namechange_reference(runner) -> None:
    mod, base, tmp = runner
    (tmp / "ev" / "H-20260925-ep-value").mkdir(parents=True)
    reg = build_registration(Path("docs/prereg/H-20260925-ep-value.md"))
    (tmp / "ev" / "H-20260925-ep-value" / "registration.json").write_text(json.dumps(reg), encoding="utf-8")
    (tmp / "ref" / "namechange.csv").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        mod.main(base)


def test_runner_writes_g2_evidence_and_enforces_trial_budget(runner, capsys) -> None:
    mod, base, tmp = runner
    folder = tmp / "ev" / "H-20260925-ep-value"
    folder.mkdir(parents=True)
    reg = build_registration(Path("docs/prereg/H-20260925-ep-value.md"))
    (folder / "registration.json").write_text(json.dumps(reg), encoding="utf-8")

    assert mod.main(base) == 0
    evidence = json.loads((folder / "G2.json").read_text(encoding="utf-8"))
    assert evidence["metrics"]["placebo_threshold"] is not None
    result = evaluate(tmp / "ev")
    g2 = next(g for g in result["hypotheses"][0]["gates"] if g["gate"] == "G2")
    assert g2["status"] in {"PASS", "FAIL"}  # 证据有效（非 INVALID/MISSING）

    assert mod.main(base) == 2  # 同一 variant 不得重跑
    assert "不得重跑" in capsys.readouterr().out
    assert mod.main([*base, "--variant", "1"]) == 0
    assert mod.main([*base, "--variant", "2"]) == 0
    trials = json.loads((folder / "trials.json").read_text(encoding="utf-8"))["trials"]
    assert [t["variant"] for t in trials] == [0, 1, 2]
    assert mod.main([*base, "--variant", "1"]) == 2
    assert "上限" in capsys.readouterr().out


# ── 历史名称抓取 ──

class _NamePro:
    def __init__(self, rows: int, *, ignore_offset: bool = False) -> None:
        self.frame = pd.DataFrame({
            "ts_code": [f"{i:06d}.SZ" for i in range(rows)], "name": "某", "start_date": "20100101",
            "end_date": None, "ann_date": "20100101", "change_reason": "",
        })
        self.ignore_offset = ignore_offset

    def namechange(self, *, fields: str, offset: int, limit: int):
        start = 0 if self.ignore_offset else offset
        return self.frame.iloc[start:start + limit]


def test_namechange_pagination_collects_all_and_refuses_truncation(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import sync_namechange

    frame = sync_namechange.fetch_all(_NamePro(12), page=5)
    assert len(frame) == 12
    with pytest.raises(RuntimeError, match="offset"):
        sync_namechange.fetch_all(_NamePro(12, ignore_offset=True), page=5)
    with pytest.raises(RuntimeError, match="满页"):
        sync_namechange.fetch_all(_NamePro(20), page=5, max_pages=2)
    meta = sync_namechange.write_reference(frame, tmp_path / "ref")
    assert meta["rows"] == 12
    assert meta["sha256"] == hashlib.sha256((tmp_path / "ref" / "namechange.csv").read_bytes()).hexdigest()
    sync_namechange.write_reference(frame, tmp_path / "ref")
    assert len(list((tmp_path / "ref").glob("namechange-*.prev.csv"))) == 1
