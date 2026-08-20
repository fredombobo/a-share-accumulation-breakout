"""Browser acceptance for the default beginner Lab and paper-trading journeys."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

captured: dict[str, object] = {"drafts": 0, "confirms": 0, "cycles": 0}


def _catalog() -> dict:
    return {
        "strategies": {
            "A": {"name": "形态突破", "tagline": "寻找整理后放量启动",
                  "entry_title": "入场", "entry_steps": ["等待突破"],
                  "exit_title": "退出", "exit_steps": ["止损或到期"], "fixed_note": ""},
            "B": {"name": "五步抓主升", "tagline": "观察趋势逐步增强",
                  "entry_title": "入场", "entry_steps": ["等待确认"],
                  "exit_title": "退出", "exit_steps": ["趋势结束"], "fixed_note": ""},
        },
        "params": [],
        "pipeline": [],
        "defaults": {"vol_ratio_min": 1.5, "strong_reset": 3,
                     "exit_window": 10, "stop_pct": 0.07},
        "grid_default": {"vol_ratio_min": [1.3, 1.5, 1.8], "strong_reset": [2, 3, 4],
                         "exit_window": [7, 10, 15], "stop_pct": [0.05, 0.07]},
        "grid_combo_count": 54,
    }


def _research() -> dict:
    return {
        "plan": {"mode": "full", "label": "完整验证窗", "n_dates": 969,
                 "earliest": "20220809", "latest": "20260807",
                 "is_start": "20230801", "is_end": "20250731",
                 "oos_start": "20250801", "oos_end": "20260731",
                 "data_ready_for_edge_validation": True},
        "need_backfill": False,
    }


def _review(scope: str = "ACCOUNT") -> dict:
    return {
        "scope": scope, "persisted": False, "can_confirm": True,
        "instrument": {"ts_code": "000001.SZ", "inst_type": "STOCK", "lot_size": 100},
        "side": "BUY", "mode": "MANUAL_HISTORY", "decision_date": "20260805",
        "execution_trade_date": "20260806",
        "quote": {"open": "10.200000", "high": "10.500000", "low": "10.100000",
                  "close": "10.400000", "volume": "10000", "revision": "000001.SZ:20260806"},
        "estimate": {"requested_qty": 100, "estimated_fill_qty": 100,
                     "max_fill_qty": 500, "fill_price": "10.210200",
                     "notional_yuan": "1021.02", "commission_yuan": "5.00",
                     "tax_yuan": "0.00", "other_fee_yuan": "0.10",
                     "reserve_yuan": "1016.62", "cash_change_yuan": "-1026.12",
                     "remaining_cash_yuan": "8983.38"},
        "checks": [{"code": "TRADING_DAY", "label": "成交日为开市日",
                    "passed": True, "message": "将使用 20260806 开盘行情"}],
        "assumptions": {"slippage_bps": 10, "commission_bps": 5,
                        "sell_tax_bps": 10, "participation_limit_pct": "5"},
    }


def _trusted_fail() -> dict:
    return {
        "research_run_id": "guided-lab", "verdict": "FAIL", "candidate_eligible": False,
        "summary": "样本外净收益未通过", "block_reasons": ["样本外净收益未通过"],
        "versions": {"dataset": "fixture", "code": "fixture", "cost": "fixture"},
        "sample": {"universe_size": 600, "step": 10, "windows": {"mode": "full"}},
        "cost_assumptions": {}, "primary_is": {}, "primary_oos": {},
        "wf_windows": [], "baselines": {}, "checks": [], "sensitivity": [],
    }


def api_route(route: Route) -> None:
    request = route.request
    path = urlparse(request.url).path
    if path == "/api/health":
        payload = {"status": "ok", "as_of": "20260807"}
    elif path == "/api/lab/catalog":
        payload = _catalog()
    elif path == "/api/lab/research-status":
        payload = _research()
    elif path == "/api/lab/status":
        payload = ({"task_id": "guided-lab", "status": "done", "phase": "CANDIDATE",
                    "progress": 100, "message": "样本外净收益未通过", "strategy": "A",
                    "result": {"is_top": [], "is_all": [], "oos": [],
                               "trusted_report": _trusted_fail()}}
                   if captured.get("lab_done") else
                   {"task_id": "guided-lab", "status": "running", "phase": "IS",
                    "progress": 12, "message": "正在检查数据", "strategy": "A"}
                   if "lab_body" in captured else {"task_id": None, "status": "idle"})
    elif path in {"/api/lab/leaderboard", "/api/lab/arena"}:
        payload = {"rows": [], "source": "fixture"}
    elif path == "/api/lab/reports":
        payload = {"items": []}
    elif path == "/api/lab/optimize":
        captured["lab_body"] = request.post_data_json
        payload = {"status": "started", "task_id": "guided-lab", "strategy": "A",
                   "research_mode": "full", "windows": _research()["plan"]}
    elif path == "/api/paper/dashboard":
        payload = {
            "account": {"account_id": 1, "cash_fen": 1_000_000, "status": "ACTIVE"},
            "equity": {"cash_fen": 1_000_000, "market_value_fen": 0,
                       "total_equity_fen": 1_000_000, "positions": 0},
            "equity_curve": [], "unresolved_reconciliation_count": 0,
            "paper_notice": "纸面仿真，不会向券商下单",
            "guide": {"next_action": "START_SIMULATION", "blocker_codes": [],
                      "pending_order": None, "earliest_simulation_date": None,
                      "latest_market_date": "20260807",
                      "unresolved_reconciliation_count": 0},
        }
    elif path == "/api/paper/trading-calendar":
        payload = {"open_dates": ["20260805", "20260806", "20260807"],
                   "earliest_simulation_date": None, "latest_market_date": "20260807"}
    elif path == "/api/paper/orders/review":
        body = request.post_data_json
        captured["review_body"] = body
        payload = _review(str(body.get("scope") or "ACCOUNT"))
    elif path == "/api/paper/orders/drafts" and request.method == "POST":
        body = request.post_data_json
        if body.get("side") == "SELL":
            captured["sell_body"] = body
        else:
            captured["drafts"] = int(captured["drafts"]) + 1
        payload = {"order_id": "ORD-GUIDED", "source": "MANUAL_HISTORY",
                   "ts_code": "000001.SZ", "side": body.get("side", "BUY"), "qty": 100,
                   "state": "DRAFT", "reserve_fen": 0, "eligible_trade_date": "20260806",
                   "reject_reason": None, "created_at": "2026-08-08T00:00:00+08:00"}
    elif path == "/api/paper/orders/ORD-GUIDED/confirm":
        captured["confirms"] = int(captured["confirms"]) + 1
        payload = {"order_id": "ORD-GUIDED", "source": "MANUAL_HISTORY",
                   "ts_code": "000001.SZ", "side": "BUY", "qty": 100,
                   "state": "CONFIRMED", "reserve_fen": 101662,
                   "eligible_trade_date": "20260806", "reject_reason": None,
                   "created_at": "2026-08-08T00:00:00+08:00"}
    elif path == "/api/paper/cycles/run":
        captured["cycles"] = int(captured["cycles"]) + 1
        payload = {"filled_count": 1, "zero_fill_count": 0,
                   "mark": {"cash_fen": 897388, "market_value_fen": 104000,
                            "total_asset_fen": 1001388, "unrealized_pnl_fen": 2988,
                            "trade_date": "20260806", "holdings": []},
                   "reconciliation": {"result": "OK", "diffs": []}, "snapshot_ok": True}
    elif path == "/api/paper/positions":
        payload = {"positions": [{"ts_code": "000001.SZ", "total_qty": 200,
                                    "sellable_qty": 100, "avg_cost_micro": 10_000_000}]}
    elif path == "/api/paper/orders":
        payload = {"orders": []}
    elif path == "/api/paper/fills":
        payload = {"fills": []}
    elif path in {"/api/paper/reconciliation", "/api/paper/corporate-actions"}:
        payload = {"items": []}
    elif path == "/api/paper/gates/status":
        payload = {"paper_enabled": True}
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(5_000)
        page.route("**/api/**", api_route)

        page.goto("http://127.0.0.1:8000/lab", wait_until="networkidle")
        page.get_by_text("小白模式", exact=True).first.wait_for()
        page.get_by_role("button", name="开始可信验证方案 A").click()
        assert captured["lab_body"] == {
            "strategy": "A", "max_codes": 600, "step": 10, "mode": "grid",
            "grid": _catalog()["grid_default"],
        }
        page.get_by_role("list", name="操作步骤").get_by_text("数据检查").wait_for()
        page.get_by_role("button", name=re.compile("纸面交易")).click()
        page.wait_for_url("**/paper")
        page.get_by_text("实验进行中 12%", exact=True).wait_for()
        page.get_by_role("button", name=re.compile("策略实验室")).click()
        page.wait_for_url("**/lab")
        captured["lab_done"] = True
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.get_by_role("heading", name="当前不建议使用", exact=True).wait_for()
        page.get_by_role("button", name="专业视图", exact=True).click()
        page.get_by_text("① 方案说明书", exact=True).wait_for()

        page.goto("http://127.0.0.1:8000/paper", wait_until="networkidle")
        page.get_by_text("今天要做什么", exact=True).wait_for()
        page.get_by_label("模拟成交日期").fill("2026-08-06")
        page.get_by_label("股票代码", exact=True).fill("000001")
        page.get_by_label("买入数量").fill("100")
        page.get_by_role("button", name="检查并预览").click()
        page.get_by_text("预计成交价", exact=True).wait_for()
        assert captured["review_body"] == {
            "scope": "ACCOUNT", "side": "BUY", "mode": "MANUAL_HISTORY",
            "ts_code": "000001", "execution_trade_date": "20260806", "qty": 100,
        }
        page.get_by_role("button", name="确认模拟订单").click()
        page.get_by_role("button", name="按 20260806 开盘模拟成交").wait_for()
        assert captured["drafts"] == captured["confirms"] == 1
        page.get_by_role("button", name="按 20260806 开盘模拟成交").click()
        page.get_by_text("模拟成交完成", exact=True).wait_for()
        assert captured["cycles"] == 1

        page.get_by_role("button", name="第一次使用演练").click()
        page.get_by_text("演练数据，不影响你的纸面账户", exact=True).wait_for()
        page.get_by_role("button", name="运行隔离演练").click()
        page.get_by_text("隔离演练结果", exact=True).wait_for()
        assert captured["review_body"]["scope"] == "TUTORIAL"

        page.get_by_role("button", name="模拟卖出 000001.SZ").click()
        page.get_by_label("卖出数量").fill("100")
        page.get_by_role("button", name="创建待确认卖出").click()
        assert captured["sell_body"] == {"side": "SELL", "ts_code": "000001.SZ", "qty": 100}

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.route("**/api/**", api_route)
        mobile.goto("http://127.0.0.1:8000/paper", wait_until="networkidle")
        dimensions = mobile.evaluate("({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})")
        assert dimensions["scroll"] <= dimensions["inner"], dimensions
        browser.close()
        print({"lab_preset": True, "paper_three_steps": True, "guided_sell": True,
               "tutorial_isolated": True, "mobile_no_overflow": True})


if __name__ == "__main__":
    main()
