"""Browser acceptance for global, overview, scan, and stock-detail buttons.

Every API request is intercepted so the test cannot start a real scan, mutate a
paper account, or interfere with a running Lab task.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Route, sync_playwright

BASE = "http://127.0.0.1:8000"
captured: dict[str, object] = {
    "overview_pools": [],
    "sector_days": [],
    "stock_flow_days": [],
    "scan_posts": 0,
    "scan_cancels": 0,
    "scan_cancelled": False,
}


def _overview(pool: str) -> dict:
    label = {"A": "甲测试", "B": "乙测试", "ALL": "全测试"}.get(pool, "甲测试")
    return {
        "as_of": "20260807",
        "count": 1,
        "pool": pool,
        "items": [{
            "ts_code": "000001.SZ", "code": "000001", "name": label,
            "price": 10.4, "industry": "银行", "mv_yi": 100.0,
            "pe": 8.0, "pb": 1.0, "turnover": 2.0, "score": 88.0,
            "box_days": 20, "box_amp": 0.08, "vol_ratio": 1.8,
            "fund_net_wan": 1200.0, "fund_ratio": 2.0,
            "breakout_date": "20260807", "reasons": "形态成立；资金流入",
            "pool": "A" if pool != "B" else "B", "tier": "strict",
            "tradeable": pool != "B", "kline": [],
        }],
        "freshness": {"as_of": "20260807", "today": "20260810",
                      "stale_days": 1, "is_stale": False, "label": "可用"},
        "regime": {"regime": "attack", "label": "进攻", "allow_new_entries": True},
        "pool_totals": {"A": 1, "B": 1},
    }


def _stock() -> dict:
    kline = []
    for index in range(25):
        close = 10 + index / 100
        kline.append({
            "trade_date": f"202607{index + 1:02d}", "open": close - 0.03,
            "high": close + 0.08, "low": close - 0.08, "close": close,
            "vol": 100_000 + index,
        })
    return {
        "ts_code": "000001.SZ", "name": "平安银行", "industry": "银行",
        "area": "深圳", "list_date": "19910403", "kline": kline,
        "signal": {"box_high": 10.3, "box_low": 9.8, "box_days": 20,
                   "box_amp": 0.05, "breakout_date": "2026-07-25",
                   "breakout_vol_ratio": 1.8, "breakout_pct_chg": 0.03,
                   "vol_shrink_ratio": 0.7, "ma5": 10.2, "ma10": 10.1,
                   "ma20": 10.0, "reasons": ["形态成立"]},
        "fundamentals": {"pe": 8.0, "pb": 1.0, "total_mv_wan": 1_000_000,
                         "circ_mv_wan": 900_000, "turnover_rate": 2.0,
                         "volume_ratio": 1.5, "close": 10.24},
        "fund_flow": {"net_wan": 1200.0, "score": 80.0,
                      "ratio_pct": 2.0, "days": 5},
        "fina": [], "as_of": "20260807",
    }


def _stock_flow(days: int) -> dict:
    return {
        "ts_code": "000001.SZ", "name": "平安银行", "industry": "银行",
        "days": days, "as_of": "20260807",
        "stock_flow": [{
            "trade_date": f"2026080{i}", "net_wan": 100.0 * i,
            "buy_main_wan": 200.0 * i, "sell_main_wan": 100.0 * i,
            "buy_elg_wan": 100.0 * i, "buy_lg_wan": 100.0 * i,
        } for i in range(1, min(days, 5) + 1)],
        "sector_flow": {"dates": ["20260801", "20260802"],
                        "net_wan": [100.0, -50.0]},
    }


def api_route(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = parsed.path
    query = parse_qs(parsed.query)

    if path == "/api/health":
        payload = {"status": "ok", "as_of": "20260807"}
    elif path == "/api/setup-status":
        payload = {"has_token": True, "has_frontend_dist": True,
                   "latest_daily": "20260807", "latest_moneyflow": "20260807",
                   "has_market_data": True, "scan_result_rows": 1,
                   "ui_mode": "dist", "open_url": BASE, "tips": []}
    elif path == "/api/lab/status":
        payload = {"task_id": None, "status": "idle", "progress": 0}
    elif path == "/api/overview":
        pool = query.get("pool", ["A"])[0]
        captured["overview_pools"].append(pool)
        payload = _overview(pool)
    elif path == "/api/sector-flow":
        days = int(query.get("days", ["10"])[0])
        captured["sector_days"].append(days)
        payload = {"dates": ["20260806", "20260807"], "days": days,
                   "industries": {"银行": [100.0, 200.0]},
                   "top_in": [{"industry": "银行", "net_wan": 300.0}],
                   "top_out": [{"industry": "煤炭", "net_wan": -200.0}]}
    elif path == "/api/money-heatmap":
        payload = {"trade_date": "20260807", "total_wan": 200.0,
                   "items": [{"name": "银行", "value": 300.0, "net_wan": 300.0},
                             {"name": "煤炭", "value": 100.0, "net_wan": -100.0}]}
    elif path == "/api/scan" and request.method == "POST":
        captured["scan_posts"] = int(captured["scan_posts"]) + 1
        payload = {"status": "started", "task_id": "scan-button-audit",
                   "top": 20, "days": 160}
    elif path == "/api/scan/status":
        if query.get("task_id"):
            payload = ({"id": "scan-button-audit", "status": "cancelled",
                        "stage": "已取消", "progress": 5, "cancel_requested": True}
                       if captured["scan_cancelled"] else
                       {"id": "scan-button-audit", "status": "running",
                        "stage": "扫描中", "progress": 5, "cancel_requested": False})
        else:
            payload = {"id": "", "status": "idle", "stage": "空闲",
                       "progress": 0, "cancel_requested": False}
    elif path == "/api/scan/scan-button-audit/cancel" and request.method == "POST":
        captured["scan_cancels"] = int(captured["scan_cancels"]) + 1
        captured["scan_cancelled"] = True
        payload = {"status": "cancelling", "stage": "取消中"}
    elif path == "/api/stock/000001.SZ":
        payload = _stock()
    elif path == "/api/stock/000001.SZ/flow":
        days = int(query.get("days", ["20"])[0])
        captured["stock_flow_days"].append(days)
        payload = _stock_flow(days)
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.add_init_script("localStorage.clear()")
        page.set_default_timeout(5_000)
        page.route("**/api/**", api_route)
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)

        page.goto(BASE, wait_until="networkidle")
        page.get_by_text("甲测试", exact=True).wait_for()

        # Pool and sector-period buttons must request and render their own data.
        page.get_by_role("button", name="B 观察", exact=True).click()
        page.get_by_text("乙测试", exact=True).wait_for()
        page.get_by_role("button", name="全部", exact=True).click()
        page.get_by_text("全测试", exact=True).wait_for()
        page.get_by_role("button", name="A 可交易", exact=True).click()
        page.get_by_text("甲测试", exact=True).wait_for()
        for days in (5, 20, 10):
            # Direct DOM click avoids Playwright waiting on ECharts' render
            # scheduling; the observable contract is the requested period.
            page.get_by_role("button", name=f"{days}日", exact=True).first.evaluate(
                "element => element.click()"
            )
            page.wait_for_timeout(150)
        assert {5, 10, 20}.issubset(set(captured["sector_days"]))

        # Theme toggle provides an observable state change.
        theme_button = page.locator("button.theme-toggle")
        before_theme = page.locator("html").get_attribute("data-theme")
        theme_button.click()
        after_theme = page.locator("html").get_attribute("data-theme")
        assert before_theme != after_theme

        # Invalid search explains the format; a valid code opens stock detail.
        search = page.get_by_label("股票代码搜索")
        search.fill("abc")
        page.get_by_role("button", name="查询", exact=True).click()
        page.get_by_text("格式：000001 或 000001.SZ / 600000.SH", exact=True).wait_for()
        search.fill("000001")
        page.get_by_role("button", name="查询", exact=True).click()
        page.wait_for_url("**/stock/000001.SZ")
        page.get_by_role("heading", name="平安银行 000001.SZ").wait_for()

        # All stock-flow periods must trigger the matching request.
        for days in (5, 10, 20):
            page.get_by_role("button", name=f"{days}日", exact=True).evaluate(
                "element => element.click()"
            )
            page.wait_for_timeout(150)
        assert {5, 10, 20}.issubset(set(captured["stock_flow_days"]))
        page.get_by_role("button", name="← 返回总览", exact=True).click()
        page.wait_for_url(BASE + "/")

        # Scan and cancel are mocked; this proves both buttons dispatch correctly
        # without launching the real child process.
        page.get_by_role("button", name="🚀 扫描(A池优先)", exact=True).click()
        page.get_by_role("button", name="⏹ 取消", exact=True).wait_for()
        page.get_by_role("button", name="⏹ 取消", exact=True).click()
        page.get_by_text("取消请求已发送", exact=True).wait_for()
        assert captured["scan_posts"] == captured["scan_cancels"] == 1

        # Sidebar navigation buttons remain operational after all interactions.
        page.get_by_role("button", name="🧪 策略实验室 研究 · 非下单").click()
        page.wait_for_url("**/lab")
        page.get_by_role("button", name="📋 纸面交易 仿真 · 不下单").click()
        page.wait_for_url("**/paper")
        page.get_by_role("button", name="📈 选股总览 A池可交易").click()
        page.wait_for_url(BASE + "/")

        browser.close()
        assert not console_errors, console_errors
        print({"global_search_theme": True, "overview_pools": captured["overview_pools"],
               "sector_days": captured["sector_days"],
               "stock_flow_days": captured["stock_flow_days"],
               "scan_cancel": True, "sidebar_navigation": True,
               "console_errors": len(console_errors)})


if __name__ == "__main__":
    main()
