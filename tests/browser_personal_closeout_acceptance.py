"""Live AB-only browser acceptance. Read-only APIs and input previews, no jobs."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8001"


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(BASE + "/", wait_until="networkidle", timeout=90000)
            health = page.request.get(BASE + "/api/health").json()
            assert health["product"] == "accumulation_breakout" and not health["live_trading_enabled"]
            expect(page.locator(".sync-text")).to_contain_text(health["as_of"], timeout=30000)
            assert page.locator(".sidebar").get_by_text("门禁阻断", exact=True).count() == 0
            page.get_by_role("button", name="使用说明", exact=False).click()
            expect(page).to_have_url(BASE + "/guide")
            expect(page.get_by_role("heading", name="从更新行情到读懂回测")).to_be_visible()
            page.get_by_role("button", name="研究回测", exact=False).click()
            expect(page).to_have_url(BASE + "/backtest")
            expect(page.get_by_role("heading", name="多参数研究回测")).to_be_visible(timeout=60000)
            before = page.request.get(BASE + "/api/backtest/latest", timeout=60000).json()["task"]
            if before and before["status"] in {"pending", "running", "cancelling"}:
                expect(page.get_by_role("button", name="检查参数空间", exact=True)).to_be_disabled()
                expect(page.locator(".task-status")).to_contain_text(before["task_id"])
                page.get_by_role("button", name="使用说明", exact=False).click()
                page.get_by_role("button", name="研究回测", exact=False).click()
                expect(page.locator(".task-status")).to_contain_text(before["task_id"], timeout=60000)
                page.reload(wait_until="networkidle")
                expect(page.locator(".task-status")).to_contain_text(before["task_id"], timeout=60000)
                assert not errors, errors
                print(json.dumps({"status": "PASS", "scenario": "running_navigation_and_reload",
                                  "task_id": before["task_id"], "business_writes": 0}, ensure_ascii=False))
                return
            page.locator("#explicit-codes").fill("000001.SZ")
            page.get_by_role("button", name="检查参数空间", exact=True).click()
            dialog = page.get_by_role("dialog", name="参数检查未通过")
            expect(dialog).to_be_visible(timeout=30000)
            assert page.evaluate("document.querySelector('dialog').contains(document.activeElement)")
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            page.locator("#explicit-codes").fill("")
            page.locator("#max-codes").fill("20")
            page.get_by_role("button", name="检查参数空间", exact=True).click()
            dialog = page.get_by_role("dialog", name="参数检查通过")
            expect(dialog).to_be_visible(timeout=180000)
            page.get_by_role("button", name="查看冻结预览", exact=True).click()
            expect(page.get_by_role("region", name="回测预览")).to_contain_text("沪市")
            expect(page.get_by_role("region", name="回测预览")).to_contain_text("深市")
            expect(page.get_by_role("region", name="回测预览")).to_contain_text("预热")
            after = page.request.get(BASE + "/api/backtest/latest", timeout=60000).json()["task"]
            assert (before or {}).get("task_id") == (after or {}).get("task_id"), "预览不应创建研究任务"
            if after and (after.get("result") or {}).get("account_details"):
                expect(page.get_by_role("region", name="入选参数账户明细")).to_be_visible()
                page.get_by_role("button", name="样本内", exact=True).click()
                page.get_by_role("button", name="样本外", exact=True).click()
            folder = ROOT / "runtime" / "ui_shots"
            folder.mkdir(exist_ok=True)
            page.screenshot(path=str(folder / "personal-closeout-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_load_state("networkidle")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "窄屏存在整页横向溢出"
            page.get_by_role("button", name="检查参数空间", exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(folder / "personal-closeout-mobile.png"), full_page=True)
            page.reload(wait_until="networkidle")
            restored = page.request.get(BASE + "/api/backtest/latest", timeout=60000).json()["task"]
            assert (after or {}).get("task_id") == (restored or {}).get("task_id")
            assert not errors, errors
            print(json.dumps({"status": "PASS", "build": health["build_version"], "date": health["as_of"],
                              "task_id": (after or {}).get("task_id"), "viewport": [1440, 390],
                              "page_errors": errors, "business_writes": 0}, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
