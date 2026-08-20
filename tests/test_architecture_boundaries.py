"""P0.3 架构与硬门测试：依赖边界 / LIVE 硬门 / NO_REPLACE_SQL。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_api_layer_does_not_import_sqlite_or_subprocess():
    r = subprocess.run(
        [sys.executable, "scripts/check_architecture.py"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_architecture_strict_has_zero_debt():
    r = subprocess.run(
        [sys.executable, "scripts/check_architecture.py", "--strict"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "存量债务" not in (r.stdout + r.stderr)


def test_live_trading_flag_fails_platform_config():
    from ab_screener.application.platform_config import PlatformConfigError, load_resolved_config

    with pytest.raises(PlatformConfigError):
        load_resolved_config(env={"LIVE_TRADING_ENABLED": "true"})
    with pytest.raises(PlatformConfigError):
        load_resolved_config(env={}, live_trading_override=True)


def test_live_trading_guard_in_backend_module():
    """backend_app 模块级 LIVE_TRADING_ENABLED=true 必须抛错（读取源码断言）。"""
    src = (ROOT / "web" / "backend_app.py").read_text(encoding="utf-8")
    assert "LIVE_TRADING_ENABLED" in src
    assert 'raise RuntimeError("LIVE_TRADING_ENABLED' in src


def test_no_replace_sql_in_production_code():
    """生产代码禁止 INSERT OR REPLACE（账本/证据只追加，不覆盖）。"""
    py_files = []
    for base in ("ab_screener", "paper_trading", "logic_platform", "web"):
        py_files.extend((ROOT / base).rglob("*.py"))
    py_files += [ROOT / "signals.py", ROOT / "scoring.py", ROOT / "local_store.py",
                 ROOT / "data_fetch.py", ROOT / "run_screener.py", ROOT / "optimizer.py"]
    hits = []
    for path in sorted(set(py_files)):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"""')):
                continue
            if "INSERT OR REPLACE" in line.upper():
                hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not hits, "生产代码仍含 INSERT OR REPLACE:\n" + "\n".join(hits)


def test_quality_gate_script_exists_and_mentions_all_stages():
    script = ROOT / "scripts" / "quality_gate.ps1"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    for keyword in ("pytest", "ruff", "mypy", "check_architecture"):
        assert keyword in text.lower(), f"quality_gate 缺少 {keyword}"
