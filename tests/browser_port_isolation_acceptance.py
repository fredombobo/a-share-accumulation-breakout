"""Read-only browser acceptance for concurrent AB Screener and AETF Alpha."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

AB_URL = "http://127.0.0.1:3001/"
AETF_URL = "http://127.0.0.1:8000/lab"


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
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )

        page.goto(AB_URL, wait_until="networkidle")
        page.get_by_text("今天只做这一件事", exact=True).wait_for()
        body = page.locator("body").inner_text()
        assert "网络/接口异常" not in body
        assert "正在判断今日状态" not in body
        health = page.evaluate(
            "async () => (await fetch('/api/health')).json()"
        )
        assert health["status"] == "ok"
        assert health.get("build_version") or health.get("guided_ui_enabled") is not None

        aetf = browser.new_page()
        aetf.goto(AETF_URL, wait_until="networkidle")
        assert "AETF Alpha" in aetf.title()

        # React StrictMode intentionally aborts the first effect pass in the
        # development server. Only non-abort network failures are defects.
        unexpected_failures = [
            failure for failure in failed_requests if "ERR_ABORTED" not in failure
        ]
        assert console_errors == [], console_errors
        assert unexpected_failures == [], unexpected_failures
        browser.close()

    print("BROWSER_PORT_ISOLATION_OK")


if __name__ == "__main__":
    main()
