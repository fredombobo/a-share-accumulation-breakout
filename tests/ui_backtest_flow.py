"""回测工作台浏览器全流程验收：填参数 → 运行 → 等待结果 → 截图。"""
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
        # 填参数：宇宙 200、步长 30、关 WF（加速）——按标签定位可见输入框
        page.locator(".bt-side .lab-field", has_text="宇宙股票数").locator("input").fill("200")
        page.locator(".bt-side .lab-field", has_text="采样步长").locator("input").fill("30")
        # 取消勾选 WF
        wf_cb = page.locator(".bt-side input[type=checkbox]").first
        if wf_cb.is_checked():
            wf_cb.click()
        page.screenshot(path=str(OUT / "10-backtest-form-dark.png"))
        # 运行
        page.locator(".bt-side button[type=submit]").click()
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "11-backtest-running.png"))
        # 等待完成（最多 6 分钟）
        deadline = time.time() + 360
        done = False
        while time.time() < deadline:
            page.wait_for_timeout(4000)
            if page.locator(".bt-results").count() > 0:
                done = True
                break
            if page.locator(".empty").count() > 0 and page.text_content(".empty") and "失败" in (page.text_content(".empty") or ""):
                break
        if done:
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "12-backtest-results.png"))
            print("[flow] results rendered OK")
        else:
            print("[flow] TIMEOUT waiting for results")
        print("console errors:", errors[:8] if errors else "(none)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
