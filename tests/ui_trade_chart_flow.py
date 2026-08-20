"""回测工作台 K 线标注交互验收：跑回测 → 点击逐笔行 → K 线面板 + 播放 → 截图。"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime" / "ui_shots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8001"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1560, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR {e}"))

        page.goto(BASE + "/backtest", wait_until="networkidle")
        page.wait_for_timeout(800)
        page.locator(".bt-side .lab-field", has_text="宇宙股票数").locator("input").fill("200")
        page.locator(".bt-side .lab-field", has_text="采样步长").locator("input").fill("40")
        for cb in page.locator(".bt-side input[type=checkbox]").all():
            try:
                if cb.is_checked():
                    cb.click(force=True, timeout=2000)
            except Exception:  # noqa: BLE001
                pass
        page.locator(".bt-side button[type=submit]").click()
        deadline = time.time() + 300
        while time.time() < deadline:
            page.wait_for_timeout(4000)
            if page.locator(".bt-results").count() > 0:
                break
        if page.locator(".bt-results").count() == 0:
            print("[flow] TIMEOUT waiting for results")
            return 1
        page.wait_for_timeout(1000)
        # 点击第一笔已成交交易行 → K 线面板
        rows = page.locator(".bt-results .lab-table tbody tr")
        print("trade rows:", rows.count())
        rows.first.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        rows.first.click(force=True)
        page.wait_for_timeout(1200)
        has_chart = page.locator(".trade-chart").count() > 0
        has_canvas = page.locator(".trade-chart canvas").count() > 0
        print("trade-chart panel:", has_chart, "| canvas:", has_canvas)
        page.screenshot(path=str(OUT / "13-trade-chart.png"))
        # 播放
        if has_chart:
            page.locator(".trade-chart .btn").click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "14-trade-chart-playing.png"))
            page.locator(".trade-chart .btn").click()
        print("console errors:", errors[:6] if errors else "(none)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
