"""Reproducible Playwright acceptance for Lab restore/report/mobile behavior."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

BASE = "http://127.0.0.1:8000"
SCREENSHOT = Path(__file__).resolve().parents[1] / "runtime" / "lab-trusted-report-acceptance.png"


def _report() -> dict:
    check = {
        "id": "oos_pf", "label": "OOS 净 PF", "passed": False,
        "actual": 0.91, "threshold": ">= 1.0",
    }
    return {
        "research_run_id": "restore-demo", "verdict": "FAIL", "candidate_eligible": False,
        "summary": "OOS 净 PF 未通过（实际 0.91，要求 >= 1.0）",
        "block_reasons": ["OOS 净 PF 未通过（实际 0.91，要求 >= 1.0）"],
        "versions": {"dataset": "daily-demo", "code": "code-demo", "cost": "cost-demo"},
        "sample": {"universe_size": 200, "step": 10, "windows": {"mode": "full"}},
        "cost_assumptions": {"notional": 100000, "slippage_each_side": 0.001},
        "primary_is": {"param_id": "p1", "net_profit_factor": 1.2},
        "primary_oos": {"param_id": "p1", "oos_net_profit_factor": 0.91, "oos_net_max_drawdown": 0.2},
        "wf_windows": [
            {"window": f"WF{i}", "train_pf": 1.2, "test_pf": 1.0, "test_n": 35, "test_dd": 0.2}
            for i in range(1, 4)
        ],
        "baselines": {
            "random": {"n_trades": 40, "net_avg_return": 0.001, "net_profit_factor": 1.0, "net_max_drawdown": 0.1},
            "ma20_60": {"n_trades": 40, "net_avg_return": 0.002, "net_profit_factor": 1.0, "net_max_drawdown": 0.1},
        },
        "checks": [check], "sensitivity": [{"param_id": "p2"}, {"param_id": "p3"}],
    }


def main() -> None:
    state = {"status": "running", "calls": 0}
    report = _report()

    def status_payload() -> dict:
        state["calls"] += 1
        if state["status"] == "running":
            return {
                "task_id": "restore-demo", "status": "running", "phase": "WF",
                "progress": 62, "message": "三窗口 Walk-forward 净成本复核", "strategy": "A",
            }
        metric = {
            "param_id": "p1", "strategy": "A", "vol_ratio_min": 1.5,
            "strong_reset": 3, "exit_window": 10, "stop_pct": 0.07,
            "net_n_trades": 40, "net_win_rate": 0.4, "net_profit_factor": 1.2,
            "net_max_drawdown": 0.2, "oos_net_n_trades": 40,
            "oos_net_win_rate": 0.4, "oos_net_profit_factor": 0.91,
            "oos_net_max_drawdown": 0.2,
        }
        return {
            "task_id": "restore-demo", "status": "done", "phase": "CANDIDATE", "progress": 100,
            "message": report["summary"], "strategy": "A",
            "result": {"is_top": [metric], "is_all": [metric], "oos": [metric], "trusted_report": report},
        }

    def intercept(route: Route) -> None:
        if "/api/lab/status" in route.request.url:
            route.fulfill(json=status_payload())
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.add_init_script("localStorage.setItem('ab.ui.mode.lab.v1', 'advanced')")
        errors: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.route("**/api/lab/status*", intercept)
        page.goto(f"{BASE}/lab")
        page.wait_for_load_state("networkidle")
        page.set_default_timeout(5_000)
        page.get_by_text("restore-demo", exact=True).wait_for()
        progress = page.locator(".lab-progress")
        assert "[WF]" in progress.inner_text()

        page.get_by_role("button", name="① 方案说明书", exact=True).click()
        progress.wait_for(state="hidden")
        page.get_by_role("button", name="实验进行中 62%", exact=True).click()
        progress.wait_for()

        page.get_by_role("button", name=re.compile("选股总览")).click()
        page.get_by_role("button", name=re.compile("策略实验室")).click()
        page.get_by_text("restore-demo", exact=True).wait_for()
        calls_after_remount = state["calls"]
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(200)
        assert state["calls"] > calls_after_remount

        state["status"] = "done"
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        conclusion = page.get_by_text("可信研究结论：FAIL", exact=True)
        conclusion.wait_for()
        assert page.get_by_text("人话阻断原因", exact=True).is_visible()
        assert page.locator('a[href*="format=markdown"]').is_visible()
        assert page.locator('a[href*="format=json"]').is_visible()

        # The global completed-task button must open the conclusion even when
        # the user is already on /lab but has switched away from the results tab.
        page.get_by_role("button", name="① 方案说明书", exact=True).click()
        conclusion.wait_for(state="hidden")
        page.get_by_role("button", name="实验已完成 · 查看结论", exact=True).click()
        conclusion.wait_for()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.add_init_script("localStorage.setItem('ab.ui.mode.lab.v1', 'advanced')")
        mobile.route("**/api/lab/status*", intercept)
        mobile.goto(f"{BASE}/lab")
        mobile.wait_for_load_state("networkidle")
        mobile.get_by_text("可信研究结论：FAIL", exact=True).wait_for()
        dimensions = mobile.evaluate("({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})")
        assert dimensions["scroll"] <= dimensions["inner"], dimensions
        mobile.screenshot(path=str(SCREENSHOT), full_page=True)

        browser.close()
        assert not errors, errors
        print({"restore_calls": state["calls"], "desktop_report": True, "mobile_no_overflow": True,
               "console_errors": len(errors), "screenshot": str(SCREENSHOT)})


if __name__ == "__main__":
    main()
