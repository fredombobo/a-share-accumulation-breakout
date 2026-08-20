"""Exercise every paper-workbench button state with intercepted APIs."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

BASE = "http://127.0.0.1:8000"
state: dict[str, object] = {
    "draft_sides": [], "confirmed": 0, "cancelled": 0, "cycles": 0,
    "previews": 0, "imports": 0, "actions": 0,
    "confirm_state": "DRAFT", "cancel_state": "CONFIRMED",
}


def _order(order_id: str, order_state: str) -> dict:
    return {
        "order_id": order_id, "source": "MANUAL_HISTORY", "ts_code": "000001.SZ",
        "side": "BUY", "qty": 100, "state": order_state, "reserve_fen": 101_000,
        "eligible_trade_date": "20260806", "reject_reason": None,
        "created_at": "2026-08-05T16:00:00+08:00",
    }


def route_api(route: Route) -> None:
    request = route.request
    path = urlparse(request.url).path

    if path == "/api/health":
        payload = {"status": "ok", "as_of": "20260807", "guided_ui_enabled": True}
    elif path == "/api/lab/status":
        payload = {"task_id": None, "status": "idle", "progress": 0}
    elif path == "/api/paper/dashboard":
        payload = {
            "account": {"account_id": 1, "currency": "CNY", "cash_fen": 1_000_000,
                        "status": "ACTIVE"},
            "equity": {"cash_fen": 1_000_000, "market_value_fen": 200_000,
                       "total_equity_fen": 1_200_000, "positions": 1},
            "equity_curve": [], "unresolved_reconciliation_count": 0,
            "risk": {"single_instrument_limit_pct": "10", "gross_exposure_limit_pct": "80",
                     "cash_buffer_pct": "10", "daily_buy_limit_pct": "20",
                     "reserved_cash_fen": 101_000},
            "guide": {"next_action": "START_SIMULATION", "blocker_codes": [],
                      "pending_order": None, "earliest_simulation_date": "20260801",
                      "latest_market_date": "20260807",
                      "unresolved_reconciliation_count": 0},
        }
    elif path == "/api/paper/trading-calendar":
        payload = {"open_dates": ["20260805", "20260806", "20260807"],
                   "earliest_simulation_date": "20260801", "latest_market_date": "20260807"}
    elif path == "/api/paper/positions":
        payload = {"positions": [{"ts_code": "000001.SZ", "total_qty": 200,
                                    "sellable_qty": 100, "avg_cost_micro": 10_000_000}]}
    elif path == "/api/paper/orders" and request.method == "GET":
        payload = {"orders": [
            _order("ORD-CONFIRM-001", str(state["confirm_state"])),
            _order("ORD-CANCEL-002", str(state["cancel_state"])),
        ]}
    elif path == "/api/paper/fills":
        payload = {"fills": []}
    elif path == "/api/paper/reconciliation":
        payload = {"items": []}
    elif path == "/api/paper/corporate-actions":
        payload = {"items": [{
            "action_id": 7, "ts_code": "000001.SZ", "ex_date": "20260807",
            "kind": "CASH_DIVIDEND", "amount_fen": 100, "ratio": None,
            "status": "APPLIED" if state["actions"] else "PENDING",
        }]}
    elif path == "/api/paper/gates/status":
        payload = {"paper_enabled": True, "live_trading_enabled": False}
    elif path == "/api/paper/orders/drafts" and request.method == "POST":
        body = request.post_data_json
        state["draft_sides"].append(body.get("side"))
        payload = _order(f"ORD-{body.get('side')}-NEW", "DRAFT")
    elif path == "/api/paper/orders/ORD-CONFIRM-001/confirm":
        state["confirmed"] = int(state["confirmed"]) + 1
        state["confirm_state"] = "CONFIRMED"
        payload = _order("ORD-CONFIRM-001", "CONFIRMED")
    elif path == "/api/paper/orders/ORD-CANCEL-002/cancel":
        state["cancelled"] = int(state["cancelled"]) + 1
        state["cancel_state"] = "CANCELLED"
        payload = _order("ORD-CANCEL-002", "CANCELLED")
    elif path == "/api/paper/cycles/run":
        state["cycles"] = int(state["cycles"]) + 1
        payload = {"filled_count": 0, "zero_fill_count": 0,
                   "mark": {"cash_fen": 1_000_000, "market_value_fen": 200_000,
                            "total_asset_fen": 1_200_000, "unrealized_pnl_fen": 0,
                            "trade_date": "20260806", "holdings": []},
                   "reconciliation": {"result": "OK", "diffs": []}, "snapshot_ok": True}
    elif path == "/api/paper/import/preview":
        state["previews"] = int(state["previews"]) + 1
        payload = {"source_hash": "a" * 64, "valid_count": 1, "invalid_count": 0,
                   "items": [{"ts_code": "600000.SH", "shares": 100, "cost": "9.80",
                              "stop_loss": "9.00", "opened_at": "2026-08-01T00:00:00+08:00",
                              "last_close": "10.00", "valid": True, "errors": []}]}
    elif path == "/api/paper/import/commit":
        state["imports"] = int(state["imports"]) + 1
        payload = {"imported": 1, "skipped_existing": False, "positions": []}
    elif path == "/api/paper/corporate-actions/7/apply":
        state["actions"] = int(state["actions"]) + 1
        payload = {"action_id": 7, "status": "APPLIED"}
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.add_init_script("localStorage.setItem('ab.ui.mode.paper.v1', 'advanced')")
        page.set_default_timeout(15_000)
        page.on("dialog", lambda dialog: dialog.accept())
        page.route("**/api/**", route_api)
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        page.goto(f"{BASE}/paper", wait_until="networkidle")

        # Account-position action pre-fills the sell form and changes tab.
        page.get_by_role("button", name="卖出", exact=True).click()
        page.get_by_role("button", name="创建卖出", exact=True).click()
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=paper-historical-buy]')?.disabled"
        )
        assert state["draft_sides"] == ["SELL"], page.locator("body").inner_text()

        # Historical buy, order confirmation/cancellation, and cycle buttons.
        page.get_by_test_id("paper-simulation-date").fill("20260806")
        page.get_by_test_id("paper-buy-code").fill("000001")
        page.get_by_test_id("paper-buy-qty").fill("100")
        page.get_by_test_id("paper-historical-buy").click()
        page.wait_for_timeout(500)
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=paper-historical-buy]')?.disabled"
        )
        assert state["draft_sides"] == ["SELL", "BUY"]
        confirm_row = page.locator("tr", has_text="ORD-CONFIRM-")
        confirm_row.get_by_role("button", name="确认", exact=True).click()
        page.wait_for_timeout(500)
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=paper-historical-buy]')?.disabled"
        )
        cancel_row = page.locator("tr", has_text="ORD-CANCEL-0")
        cancel_row.get_by_role("button", name="取消", exact=True).click()
        page.wait_for_timeout(500)
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=paper-historical-buy]')?.disabled"
        )
        page.get_by_role("button", name="▶ 手动补跑日结", exact=True).click()
        page.wait_for_timeout(500)
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=paper-historical-buy]')?.disabled"
        )

        # Every workbench tab and its conditional action buttons.
        page.get_by_role("tab", name="💱 成交", exact=True).click()
        page.get_by_role("heading", name="成交记录", exact=True).wait_for()
        page.get_by_role("tab", name="📥 导入", exact=True).click()
        page.get_by_label("portfolio 路径").fill("portfolio.json")
        page.get_by_role("button", name="预览", exact=True).click()
        page.get_by_role("button", name="确认导入", exact=True).wait_for()
        page.get_by_role("button", name="确认导入", exact=True).click()
        page.wait_for_timeout(500)
        page.get_by_role("tab", name="🔍 对账", exact=True).click()
        page.get_by_role("heading", name="对账记录", exact=True).wait_for()
        page.get_by_role("tab", name="⚙️ 设置", exact=True).click()
        page.get_by_role("heading", name="公司行为调整", exact=True).wait_for()
        page.get_by_role("button", name="应用调整", exact=True).click()
        page.wait_for_timeout(500)

        # The mode switch works in both directions.
        page.get_by_role("button", name="小白模式", exact=True).click()
        page.get_by_role("heading", name="纸面交易三步模拟", exact=True).wait_for()
        page.get_by_role("button", name="专业视图", exact=True).click()
        page.get_by_role("tab", name="📊 账户", exact=True).wait_for()

        browser.close()
        assert state["draft_sides"] == ["SELL", "BUY"]
        assert state["confirmed"] == state["cancelled"] == state["cycles"] == 1
        assert state["previews"] == state["imports"] == state["actions"] == 1
        assert not console_errors, console_errors
        print({"paper_tabs": 6, "draft_sides": state["draft_sides"],
               "confirm_cancel_cycle": True, "import": True,
               "corporate_action": True, "mode_round_trip": True,
               "console_errors": len(console_errors)})


if __name__ == "__main__":
    main()
