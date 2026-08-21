from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_ab_development_ports_are_isolated_from_aetf() -> None:
    vite_config = _read("web/frontend/vite.config.ts")
    backend = _read("web/backend_app.py")
    legacy_misc = _read("ab_screener/api/routers/legacy_misc.py")
    starter = _read("start_ui.ps1")

    assert "http://127.0.0.1:8001" in vite_config
    assert "AB_BACKEND_URL" in vite_config
    assert "AB_BACKEND_PORT" in backend
    assert "port=_backend_port()" in backend
    assert 'f"http://127.0.0.1:{_backend_port()}/"' in legacy_misc
    assert "$BackendPort = 8001" in starter
    assert "Stop-PortListeners" not in starter


def test_ab_stop_script_does_not_kill_unowned_services() -> None:
    stopper = _read("stop_ui.ps1")

    assert "foreach ($port in 8000, 3001)" not in stopper
    assert "multiprocessing-fork|spawn_main" not in stopper
    assert "Stop-OwnedListener" in stopper


def test_all_ab_launchers_use_the_isolated_backend_port() -> None:
    bootstrap = _read("bootstrap.py")
    easy_start = _read("easy_start.py")

    assert "BACKEND_PORT = 8001" in bootstrap
    assert "BACKEND_PORT = 8001" in easy_start
    assert "_port_in_use(BACKEND_PORT)" in bootstrap
    assert "_port_in_use(BACKEND_PORT)" in easy_start
    assert "将释放 8000" not in easy_start
    assert "端口被非本项目服务占用，回收后重启" not in easy_start
