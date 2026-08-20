"""Browser regression for the selected historical open flowing into a paper order."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

captured: dict = {}


def api_route(route: Route) -> None:
    request = route.request
    path = urlparse(request.url).path
    if path == "/api/health":
        payload = {"status": "ok", "as_of": "20260807"}
    elif path == "/api/paper/dashboard":
        payload = {
            "account": {"account_id": 1, "currency": "CNY", "status": "ACTIVE"},
            "equity": {"cash_fen": 50_000_000, "market_value_fen": 0,
                       "total_equity_fen": 50_000_000, "positions": 0},
        }
    elif path == "/api/paper/positions":
        payload = {"positions": []}
    elif path == "/api/paper/orders" and request.method == "GET":
        payload = {"orders": []}
    elif path == "/api/paper/fills":
        payload = {"fills": []}
    elif path == "/api/paper/reconciliation" or path == "/api/paper/corporate-actions":
        payload = {"items": []}
    elif path == "/api/paper/gates/status":
        payload = {"paper_enabled": True}
    elif path == "/api/paper/orders/drafts" and request.method == "POST":
        captured.update(request.post_data_json)
        if captured.get("execution_trade_date") == "20260808":
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": {
                    "code": "NOT_TRADING_DAY",
                    "message": "Selected date is not an exchange trading day",
                    "details": {"execution_trade_date": "20260808"},
                    "retryable": False,
                }}),
            )
            return
        payload = {
            "order_id": "ORD-HISTORY", "source": "MANUAL_HISTORY",
            "ts_code": "688105.SH", "side": "BUY", "qty": 100,
            "state": "DRAFT", "reserve_fen": 0, "reserved_qty": 0,
            "signal_trade_date": "20260805", "eligible_trade_date": "20260806",
            "reject_reason": None, "created_at": "2026-08-08T00:00:00+08:00",
        }
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script("localStorage.setItem('ab.ui.mode.paper.v1', 'advanced')")
        page.set_default_timeout(5_000)
        page.route("**/api/**", api_route)
        page.goto("http://127.0.0.1:8000/paper", wait_until="networkidle")
        page.locator('button[role="tab"]').nth(1).click()
        date_input = page.get_by_test_id("paper-simulation-date")
        date_input.wait_for(state="visible")
        assert date_input.input_value() == "20260807"
        date_input.fill("20260806")
        page.get_by_test_id("paper-buy-code").fill("688105")
        page.get_by_test_id("paper-buy-qty").fill("100")
        page.get_by_test_id("paper-historical-buy").click()
        page.wait_for_timeout(100)
        assert captured == {
            "side": "BUY",
            "mode": "MANUAL_HISTORY",
            "ts_code": "688105",
            "execution_trade_date": "20260806",
            "qty": 100,
        }
        successful_payload = captured.copy()
        date_input.fill("20260808")
        page.get_by_test_id("paper-historical-buy").click()
        error_text = page.locator(".err").inner_text()
        assert error_text == (
            "⚠️ Error: [NOT_TRADING_DAY] Selected date is not an exchange trading day"
        )
        print({"historical_payload": successful_payload, "default_as_of": "20260807"})
    finally:
        browser.close()
