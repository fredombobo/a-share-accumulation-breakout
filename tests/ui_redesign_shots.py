"""UI 改版验收截图：深/浅主题 × 总览/实验室/纸面/个股，输出 PNG 到 runtime/ui_shots/。"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime" / "ui_shots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8001"


def snap(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
    print(f"[shot] {name}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1560, "height": 950})
        page.goto(BASE + "/", wait_until="networkidle")
        time.sleep(1.5)
        snap(page, "01-overview-dark")

        # 浅色主题
        page.click(".btn-icon[title*='浅色']")
        time.sleep(0.8)
        snap(page, "02-overview-light")
        page.click(".btn-icon[title*='深色']")
        time.sleep(0.5)

        # 个股详情（若有列表）
        card = page.query_selector(".stock-card")
        if card:
            card.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1.2)
            snap(page, "03-stock-detail-dark")

        # 策略实验室
        page.goto(BASE + "/lab", wait_until="networkidle")
        time.sleep(1.2)
        snap(page, "04-lab-dark")

        # 回测工作台
        page.goto(BASE + "/backtest", wait_until="networkidle")
        time.sleep(1.2)
        snap(page, "05-backtest-dark")
        page.click(".btn-icon[title*='浅色']")
        time.sleep(0.8)
        snap(page, "06-backtest-light")
        page.click(".btn-icon[title*='深色']")

        # 纸面交易
        page.goto(BASE + "/paper", wait_until="networkidle")
        time.sleep(1.2)
        snap(page, "07-paper-dark")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
