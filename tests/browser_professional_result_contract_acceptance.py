"""Read-only live acceptance for the professional backtest v1.3 result contract."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8001"
SCREENSHOT = ROOT / "runtime" / "ui_shots" / "wp97-browser-acceptance.png"


def main() -> None:
    console_errors: list[str] = []
    failed_requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: console_errors.append(f"PAGEERROR {error}"))
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )

        page.goto(f"{BASE_URL}/backtest", wait_until="networkidle", timeout=60_000)
        page.get_by_role("heading", name="多参数研究回测").wait_for()

        health = page.evaluate("async () => (await fetch('/api/health')).json()")
        catalog = page.evaluate("async () => (await fetch('/api/backtest/catalog')).json()")
        latest_before = page.evaluate(
            "async () => (await fetch('/api/backtest/latest')).json()"
        )
        assert health["status"] == "ok"
        assert health["live_trading_enabled"] is False
        assert catalog["version"] == "professional-grid-v1.3.0"
        assert any(item["key"] == "max_hold_days" for item in catalog["parameters"])

        body = page.locator("body")
        persisted_result = latest_before["task"].get("result")
        if persisted_result:
            assert body.get_by_text(
                "这是抽样研究，不是逐日完整回测", exact=True
            ).is_visible()
            assert body.get_by_text("0 笔（无验证证据）", exact=True).is_visible()
            exact_paths = bool(
                (persisted_result.get("path_analysis") or {}).get("evidence_complete")
                and persisted_result.get("independent_leaderboard")
            )
            expected_heading = (
                "入选参数与独立路径排行榜"
                if exact_paths
                else "入选参数与历史名义排行榜"
            )
            expected_column = "等效参数" if exact_paths else "路径证据"
            assert page.get_by_role("heading", name=expected_heading).is_visible()
            assert page.locator("table th", has_text=expected_column).is_visible()
            if not exact_paths:
                assert page.get_by_text(
                    "旧任务缺少权益路径哈希，排行榜未去重", exact=True
                ).is_visible()
            assert page.get_by_text("7.46%", exact=True).count() > 0
            assert page.get_by_text("4.86%", exact=True).count() > 0

        page.get_by_role("button", name="检查参数空间").click()
        dialog = page.get_by_role("dialog", name="参数检查通过")
        dialog.wait_for(timeout=60_000)
        assert "不会自动启动回测" in dialog.inner_text()
        dialog.get_by_role("button", name="查看冻结预览").click()
        page.get_by_role("button", name="启动研究回测").wait_for()

        latest_after = page.evaluate(
            "async () => (await fetch('/api/backtest/latest')).json()"
        )
        assert latest_after["task"]["task_id"] == latest_before["task"]["task_id"]

        page.get_by_role("button", name="使用说明 逻辑 · 操作 · 术语").click()
        page.get_by_role("heading", name="使用说明").wait_for()
        page.get_by_role("button", name="研究回测 参数 · OOS · 成本").click()
        page.get_by_role("heading", name="多参数研究回测").wait_for()

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"390px horizontal overflow={overflow}"
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT), full_page=True)

        unexpected_failures = [
            failure for failure in failed_requests if "ERR_ABORTED" not in failure
        ]
        assert console_errors == [], console_errors
        assert unexpected_failures == [], unexpected_failures
        browser.close()

    print("BROWSER_PROFESSIONAL_RESULT_CONTRACT_OK")


if __name__ == "__main__":
    main()
