"""龙虎榜正式 dist 浏览器 smoke；由 with_server.py 编排本地后端。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--db", required=True)
    parser.add_argument("--screenshot", default="runtime/lhb_browser_e2e.png")
    args = parser.parse_args()
    db = Path(args.db).resolve()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        seat = conn.execute(
            "SELECT subject_id FROM lhb_feature_snapshot WHERE subject_type='seat'"
            " AND window_days=60 ORDER BY available_at DESC,subject_id LIMIT 1"
        ).fetchone()
        code = conn.execute(
            "SELECT ts_code FROM lhb_event WHERE disclose_date='20260828' ORDER BY ts_code LIMIT 1"
        ).fetchone()
    if seat is None or code is None:
        raise RuntimeError("产品副本缺少席位或 20260828 事件")

    console_errors: list[str] = []
    checks: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"{args.base_url}/v2/lhb/radar", wait_until="networkidle")
        with page.expect_response(lambda response: "/api/v2/lhb/radar" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200, (info.value.url, info.value.status, info.value.text())
        radar_body = info.value.json()
        assert radar_body["source_status"] == "COMPLETE"
        checks["radar_api_items"] = len(radar_body["items"])
        assert int(checks["radar_api_items"]) > 0, radar_body
        page.locator("tbody tr").first.wait_for()
        checks["radar_rows"] = page.locator("tbody tr").count()
        assert int(checks["radar_rows"]) > 0

        page.goto(f"{args.base_url}/v2/lhb/network", wait_until="networkidle")
        with page.expect_response(lambda response: "/api/v2/lhb/network" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200
        network_body = info.value.json()
        assert network_body["source_status"] == "COMPLETE"
        checks["network_api_nodes"] = len(network_body["nodes"])
        assert int(checks["network_api_nodes"]) > 0, network_body
        page.get_by_text("只股票", exact=False).first.wait_for()
        checks["network_edges"] = page.locator("tbody tr").count()
        checks["network_nodes"] = page.get_by_text("只股票", exact=False).count()
        assert int(checks["network_nodes"]) > 0

        page.goto(f"{args.base_url}/v2/lhb/quality", wait_until="networkidle")
        with page.expect_response(lambda response: "/api/v2/lhb/quality" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200
        assert info.value.json()["source_status"] == "COMPLETE"
        page.wait_for_load_state("networkidle")
        assert '"event_count"' in page.locator("pre").inner_text()

        page.goto(f"{args.base_url}/v2/lhb/timeline", wait_until="networkidle")
        field = page.locator(".content input").first
        field.fill(str(code[0]))
        with page.expect_response(lambda response: "/api/v2/lhb/stocks/" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200
        timeline_body = info.value.json()
        checks["timeline_api_items"] = len(timeline_body["items"])
        assert int(checks["timeline_api_items"]) > 0, timeline_body
        page.locator("ol li").first.wait_for()
        checks["timeline_items"] = page.locator("ol li").count()
        assert int(checks["timeline_items"]) > 0

        page.goto(f"{args.base_url}/v2/lhb/profile", wait_until="networkidle")
        page.locator(".content input").nth(0).fill(str(seat[0]))
        with page.expect_response(lambda response: "/api/v2/lhb/seats/" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200
        profile_body = info.value.json()
        assert profile_body["source_status"] == "COMPLETE"
        assert len(profile_body["items"]) == 1, profile_body
        page.get_by_text("窗口", exact=False).first.wait_for()
        assert page.get_by_text("无画像快照").count() == 0

        page.goto(f"{args.base_url}/v2/lhb/backtest", wait_until="networkidle")
        with page.expect_response(lambda response: "/api/v2/lhb/backtest" in response.url) as info:
            page.locator(".content button[type=button]").click()
        assert info.value.status == 200
        assert info.value.json()["research_status"] == "RESEARCH_BLOCKED"
        page.wait_for_load_state("networkidle")
        page.get_by_text("RESEARCH_BLOCKED", exact=True).wait_for()
        page.screenshot(path=str(Path(args.screenshot).resolve()), full_page=True)
        browser.close()

    assert console_errors == [], console_errors
    print(json.dumps({"checks": checks, "console_errors": console_errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
