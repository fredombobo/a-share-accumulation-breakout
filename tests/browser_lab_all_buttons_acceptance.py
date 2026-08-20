"""Exercise the advanced Lab controls without starting a real experiment."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

BASE = "http://127.0.0.1:8000"
state = {"task": "idle", "optimize": 0, "cancel": 0, "board_reads": 0}


PARAMS = [
    {"key": "vol_ratio_min", "name": "建仓量比门槛", "unit": "倍", "meaning": "量能门槛",
     "affects": "入场", "default": 1.5, "options": [1.3, 1.5, 1.8], "range_hint": "1.3~1.8"},
    {"key": "strong_reset", "name": "强势日清零根数", "unit": "根", "meaning": "清零确认",
     "affects": "持有", "default": 3, "options": [2, 3, 4], "range_hint": "2~4"},
    {"key": "exit_window", "name": "二次出货窗口", "unit": "日", "meaning": "退出窗口",
     "affects": "退出", "default": 10, "options": [7, 10, 15], "range_hint": "7~15"},
    {"key": "stop_pct", "name": "兜底止损", "unit": "比例", "meaning": "最大回撤",
     "affects": "退出", "default": 0.07, "options": [0.05, 0.07], "range_hint": "5%~7%"},
]


def _catalog() -> dict:
    return {
        "strategies": {
            "A": {"id": "A", "name": "形态突破", "tagline": "整理后放量启动",
                  "entry_title": "入场", "entry_steps": ["等待突破"],
                  "exit_title": "退出", "exit_steps": ["止损或到期"], "fixed_note": "固定规则"},
            "B": {"id": "B", "name": "五步抓主升", "tagline": "趋势逐步增强",
                  "entry_title": "入场", "entry_steps": ["等待确认"],
                  "exit_title": "退出", "exit_steps": ["趋势结束"], "fixed_note": "固定规则"},
        },
        "params": PARAMS, "pipeline": [],
        "defaults": {"vol_ratio_min": 1.5, "strong_reset": 3,
                     "exit_window": 10, "stop_pct": 0.07},
        "grid_default": {"vol_ratio_min": [1.3, 1.5, 1.8], "strong_reset": [2, 3, 4],
                         "exit_window": [7, 10, 15], "stop_pct": [0.05, 0.07]},
        "grid_combo_count": 54,
    }


def _metric() -> dict:
    return {
        "param_id": "fixture-param", "strategy": "A", "vol_ratio_min": 1.5,
        "strong_reset": 3, "exit_window": 10, "stop_pct": 0.07,
        "net_n_trades": 40, "net_win_rate": 0.45, "net_profit_factor": 1.2,
        "net_max_drawdown": 0.12, "oos_net_n_trades": 20,
        "oos_net_win_rate": 0.4, "oos_net_profit_factor": 1.05,
        "oos_net_max_drawdown": 0.15, "is_net_profit_factor": 1.2,
    }


def route_api(route: Route) -> None:
    request = route.request
    path = urlparse(request.url).path
    if path == "/api/health":
        payload = {"status": "ok", "as_of": "20260807", "guided_ui_enabled": True}
    elif path == "/api/lab/catalog":
        payload = _catalog()
    elif path == "/api/lab/research-status":
        payload = {"plan": {"mode": "full", "label": "完整验证窗", "n_dates": 969,
                            "earliest": "20220809", "latest": "20260807",
                            "is_start": "20230801", "is_end": "20250731",
                            "oos_start": "20250801", "oos_end": "20260731",
                            "data_ready_for_edge_validation": True},
                   "need_backfill": False}
    elif path == "/api/lab/status":
        if state["task"] == "running":
            payload = {"task_id": "button-lab", "status": "running", "phase": "IS",
                       "progress": 20, "message": "净成本回测", "strategy": "A"}
        elif state["task"] == "cancelled":
            payload = {"task_id": "button-lab", "status": "cancelled", "phase": "IS",
                       "progress": 20, "message": "已取消", "strategy": "A"}
        else:
            payload = {"task_id": None, "status": "idle", "progress": 0}
    elif path == "/api/lab/optimize":
        state["optimize"] += 1
        state["task"] = "running"
        payload = {"status": "started", "task_id": "button-lab", "strategy": "A",
                   "research_mode": "full", "windows": {}}
    elif path == "/api/lab/button-lab/cancel":
        state["cancel"] += 1
        state["task"] = "cancelled"
        payload = {"status": "cancelled", "task_id": "button-lab", "msg": "已取消"}
    elif path in {"/api/lab/leaderboard", "/api/lab/compare"}:
        state["board_reads"] += 1
        payload = {"rows": [_metric()], "source": "fixture"}
    elif path == "/api/lab/arena":
        payload = {"rows": [], "source": "fixture"}
    elif path == "/api/lab/reports":
        payload = {"items": []}
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        page.add_init_script("localStorage.setItem('ab.ui.mode.lab.v1', 'advanced')")
        page.set_default_timeout(15_000)
        page.route("**/api/**", route_api)
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        page.goto(f"{BASE}/lab", wait_until="networkidle")

        # Playbook cards and navigation controls.
        page.get_by_role("button", name="① 方案说明书", exact=True).click()
        page.get_by_role("button", name="用方案 B 去调参", exact=True).click()
        page.get_by_role("heading", name="参数台 · 方案 B", exact=True).wait_for()
        page.get_by_role("button", name="查看方案说明书", exact=True).click()
        page.get_by_role("button", name="用方案 A 去调参", exact=True).click()

        # Grid/single mode, parameter chips, and window helper.
        combo_before = page.locator(".lab-combo-count").inner_text()
        page.get_by_role("button", name="1.3", exact=True).click()
        combo_after = page.locator(".lab-combo-count").inner_text()
        assert combo_before != combo_after
        page.get_by_role("button", name="单组试跑 人工指定一组参数，看 IS + OOS", exact=True).click()
        page.locator(".lab-field", has_text="时间窗").locator("select").select_option("manual")
        page.get_by_role("button", name="填入推荐窗", exact=True).click()

        # Run/cancel and refresh buttons dispatch only to mocked endpoints.
        page.get_by_role("button", name="试跑方案 A 单组", exact=True).click()
        page.get_by_role("button", name="取消任务", exact=True).wait_for()
        page.get_by_role("button", name="取消任务", exact=True).click()
        page.get_by_role("button", name="刷新结果", exact=True).wait_for()
        page.get_by_role("button", name="刷新结果", exact=True).click()
        assert state["optimize"] == state["cancel"] == 1

        # Result row selection and load-back-to-console button.
        page.get_by_role("button", name="③ 结果与明细", exact=True).click()
        page.locator(".lab-table tbody tr").first.click()
        page.get_by_role("button", name="载入单组试跑", exact=True).click()
        page.get_by_role("heading", name="参数台 · 方案 A", exact=True).wait_for()
        page.get_by_role("button", name="网格搜索 勾选多档参数，自动展开组合排序", exact=True).click()
        page.get_by_role("button", name="① 方案说明书", exact=True).click()

        # Mode round-trip is also a button contract.
        page.get_by_role("button", name="小白模式", exact=True).click()
        page.get_by_role("heading", name="验证一个策略是否值得继续研究", exact=True).wait_for()
        page.get_by_role("button", name="专业视图", exact=True).click()
        page.get_by_role("button", name="① 方案说明书", exact=True).wait_for()

        browser.close()
        assert state["board_reads"] >= 1
        assert not console_errors, console_errors
        print({"playbook_console_results": True, "grid_single": True,
               "run_cancel_refresh": True, "result_load": True,
               "mode_round_trip": True, "console_errors": len(console_errors)})


if __name__ == "__main__":
    main()
