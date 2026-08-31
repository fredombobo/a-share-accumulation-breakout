"""产品盘后流水线：真实步骤（fake 网关）五日副本 soak。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from ab_screener.application.lhb_product import run_lhb_product_day
from ab_screener.data.migration_registry import apply_pending
from local_store import LocalStore


class FakeProductPro:
    def top_list(self, **kwargs):
        day = str(kwargs["trade_date"])
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": day,
                    "reason": "日涨幅偏离值达到7%",
                    "amount": 200_000_000.0,
                    "l_sell": 20_000_000.0,
                    "l_buy": 80_000_000.0,
                    "l_amount": 100_000_000.0,
                    "net_amount": 60_000_000.0,
                }
            ]
        )

    def top_inst(self, **kwargs):
        day = str(kwargs["trade_date"])
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": day,
                    "exalter": "某证券深圳益田路营业部",
                    "side": "0",
                    "buy": 80_000_000.0,
                    "sell": 0.0,
                    "net_buy": 80_000_000.0,
                    "reason": "日涨幅偏离值达到7%",
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": day,
                    "exalter": "机构专用",
                    "side": "1",
                    "buy": 0.0,
                    "sell": 20_000_000.0,
                    "net_buy": -20_000_000.0,
                    "reason": "日涨幅偏离值达到7%",
                },
            ]
        )

    def hm_list(self):
        return pd.DataFrame(
            [
                {
                    "name": "候选游资甲",
                    "orgs": '["某证券深圳益田路营业部"]',
                }
            ]
        )


def _db(tmp_path: Path, days: list[str]) -> Path:
    db = tmp_path / "lhb-product-copy.db"
    LocalStore(db_path=db)
    with sqlite3.connect(str(db)) as conn:
        apply_pending(conn)
        conn.execute(
            "INSERT INTO stock_basic(ts_code,name,industry,list_date) VALUES (?,?,?,?)",
            ("000001.SZ", "平安银行", "银行", "19910403"),
        )
        for i, day in enumerate(days + ["20260817"]):
            conn.execute(
                "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount)"
                " VALUES (?,?,?,?,?,?,?,?)",
                ("000001.SZ", day, 10, 10.5, 9.8, 10.2, 1_000_000, 300_000 + i),
            )
            conn.execute(
                "INSERT INTO daily_basic(ts_code,trade_date,turnover_rate,circ_mv) VALUES (?,?,?,?)",
                ("000001.SZ", day, 8.0, 1_000_000.0),
            )
            conn.execute(
                "INSERT INTO trade_cal(cal_date,is_open,source,updated_at)"
                " VALUES (?,1,'local_infer','test')"
                " ON CONFLICT(cal_date) DO UPDATE SET is_open=excluded.is_open,"
                " source=excluded.source,updated_at=excluded.updated_at",
                (day,),
            )
        conn.commit()
    return db


def test_five_day_product_soak_runs_real_steps_and_is_auditable(tmp_path: Path):
    days = ["20260810", "20260811", "20260812", "20260813", "20260814"]
    db = _db(tmp_path, days)
    pro = FakeProductPro()
    for day in days:
        timestamp = f"{day[:4]}-{day[4:6]}-{day[6:8]}T16:30:00+08:00"
        out = run_lhb_product_day(
            db,
            day,
            holder="product-soak",
            pro=pro,
            published=True,
            now_iso=lambda timestamp=timestamp: timestamp,
        )
        assert out["status"] == "COMPLETED", out["results"]
        assert out["source_status"] == "COMPLETE"
        assert out["research_only"] is True
        assert out["may_generate_orders"] is False
        assert out["product"]["normalized"]["events"] == 1
        assert out["product"]["normalized"]["trades"] == 2
        assert out["product"]["alerts"]["dry_run"] is True

    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT disclose_date) FROM lhb_event").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM lhb_seat_trade").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(DISTINCT signal_date) FROM lhb_signal_observation").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM dag_runs WHERE mode='LHB_EOD'").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM dag_step_runs WHERE status='SUCCESS'").fetchone()[0] >= 40
        deliveries = conn.execute("SELECT DISTINCT dry_run FROM lhb_alert_delivery").fetchall()
        assert deliveries == [(1,)]
    assert all((db.parent / "lhb_reports" / f"{day}.json").is_file() for day in days)
