"""E2 拆路由回归测试（N0 E2-FIX）。

覆盖拆分时引入的两个 NameError 缺陷：
1. `legacy_scan.py` 漏导入 `_BUILD_VERSION` / `_OVERVIEW_CACHE`（扫描完成路径会 NameError）
2. `legacy_lab.py` 顶层漏 `import json`（Lab 报告 JSON 下载会 NameError）

用例与 plan N0 对齐：扫描模块绑定 / 清缓存可执行 / Lab JSON 下载。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_scan_module_binds_build_version_and_overview_cache() -> None:
    """N0-1：扫描模块命名空间内绑定了 _BUILD_VERSION 与 _OVERVIEW_CACHE。"""
    from ab_screener.api.routers import legacy_scan as m

    assert hasattr(m, "_BUILD_VERSION")
    assert isinstance(m._BUILD_VERSION, str) and m._BUILD_VERSION
    assert isinstance(m._OVERVIEW_CACHE, dict)
    assert "key" in m._OVERVIEW_CACHE
    assert "payload" in m._OVERVIEW_CACHE


def test_clear_overview_cache_resets_key_and_payload() -> None:
    """N0-2：_clear_overview_cache 清空脏缓存（不跑全市场扫描子进程）。"""
    from ab_screener.api.routers import legacy_scan as m

    m._OVERVIEW_CACHE["key"] = ("20260818", "A")
    m._OVERVIEW_CACHE["payload"] = {"count": 3, "items": [{"ts_code": "000001.SZ"}]}

    m._clear_overview_cache()

    assert m._OVERVIEW_CACHE["key"] is None
    assert m._OVERVIEW_CACHE["payload"] is None


def test_lab_report_download_json_does_not_raise_nameerror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """N0-3：Lab 报告 JSON 下载不抛 NameError，body 可 json.loads。"""
    from ab_screener.api.routers import legacy_lab
    from ab_screener.research.store import ResearchRunStore
    from local_store import LocalStore

    db = tmp_path / "lab-download.db"
    LocalStore(db)
    store = ResearchRunStore(db)
    store.create_run(
        "run-json", strategy="A", research_mode="full",
        request={}, input_hash="h", dataset_version="d", code_version="c", cost_version="k",
    )
    store.update(
        "run-json", status="done", progress=100, verdict="FAIL",
        result={"trusted_report": {"verdict": "FAIL", "summary": "回撤未通过"}},
        report_markdown="# FAIL",
    )
    monkeypatch.setattr(legacy_lab, "_LAB_STORE", store)

    resp = legacy_lab.lab_report_download("run-json", format="json")

    assert resp.media_type == "application/json"
    body = json.loads(resp.body.decode("utf-8"))
    assert body["research_run_id"] == "run-json"
    assert body["verdict"] == "FAIL"
    assert body["report"]["summary"] == "回撤未通过"
