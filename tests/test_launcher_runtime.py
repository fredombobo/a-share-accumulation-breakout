"""Offline runtime selection plus actual Windows cmd.exe launcher contracts."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

import bootstrap
import easy_start
import launcher_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


def _local_python(root: Path) -> Path:
    return root / ".venv312" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_local_312_wins_over_system_314(tmp_path, monkeypatch):
    python = _local_python(tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(runtime.sys, "executable", "system-python314")
    calls = []

    def probe(command):
        calls.append(command)
        return str(python) if command == [str(python)] else None

    monkeypatch.setattr(runtime, "_probe", probe)
    assert runtime.project_python(tmp_path) == str(python)
    assert calls == [[str(python)]]


def test_no_312_fails_without_creating_directory_or_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_base_candidates", lambda: [["python314"]])
    monkeypatch.setattr(runtime, "_probe", lambda _: None)
    with pytest.raises(runtime.RuntimeSetupError, match="未找到 Python 3.12"):
        runtime.project_python(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("has_executable", [True, False])
def test_broken_project_environment_is_not_overwritten(tmp_path, monkeypatch, has_executable):
    python = _local_python(tmp_path)
    python.parent.mkdir(parents=True)
    if has_executable:
        python.write_bytes(b"preserve")
    monkeypatch.setattr(runtime, "_probe", lambda _: None)
    with pytest.raises(runtime.RuntimeSetupError, match="未自动覆盖"):
        runtime.project_python(tmp_path)
    assert python.read_bytes() == b"preserve" if has_executable else not python.exists()


def test_fresh_environment_uses_only_probed_312(tmp_path, monkeypatch):
    python = str(_local_python(tmp_path))
    answers = iter([None, "python312", python])
    monkeypatch.setattr(runtime, "_base_candidates", lambda: [["python314"], ["python312"]])
    monkeypatch.setattr(runtime, "_probe", lambda _: next(answers))
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0))
    assert runtime.project_python(tmp_path) == python
    assert calls == [["python312", "-m", "venv", str(tmp_path / ".venv312")]]


@pytest.mark.parametrize("stdout,code,expected", [
    ('[[3, 14], "python314"]', 0, None),
    ('[[3, 12], "python312"]', 0, "python312"),
    ('[[3, 12], "python312"]', 1, None),
    ("not json", 0, None),
])
def test_probe_checks_real_version_and_exit_code(monkeypatch, stdout, code, expected):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=code))
    assert runtime._probe(["python"]) == expected


def test_launchers_share_runtime_and_dependency_policy(tmp_path, monkeypatch):
    for module in (easy_start, bootstrap):
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "project_python", lambda root: str(root / "verified312"))
        calls = []
        monkeypatch.setattr(module, "ensure_dependencies", lambda py, root, calls=calls: calls.append((py, root)))
        selected = module._find_python()
        (module._pip_install if module is easy_start else module.pip_install)(selected)
        assert calls == [(str(tmp_path / "verified312"), tmp_path)]


def test_satisfied_dependencies_do_not_run_pip_install(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_probe", lambda _: "python312")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0))
    runtime.ensure_dependencies("python312", tmp_path)
    assert all("install" not in command for command in calls)
    assert calls[-1] == ["python312", "-m", "pip", "check"]


@pytest.mark.parametrize("install_code", [0, 1])
def test_missing_dependencies_use_lock_and_propagate_failure(tmp_path, monkeypatch, install_code):
    (tmp_path / "requirements-lock-py312.txt").touch()
    monkeypatch.setattr(runtime, "_probe", lambda _: "python312")
    codes = iter([1, install_code, 0])
    calls = []

    def run(command, **_):
        calls.append(command)
        return SimpleNamespace(returncode=next(codes))

    monkeypatch.setattr(runtime.subprocess, "run", run)
    if install_code:
        with pytest.raises(runtime.RuntimeSetupError, match="依赖安装失败"):
            runtime.ensure_dependencies("python312", tmp_path)
    else:
        runtime.ensure_dependencies("python312", tmp_path)
    assert "-c" in calls[1]
    assert "--pre" not in calls[1]
    assert len(calls) == (2 if install_code else 3)


def test_bad_runtime_never_runs_pip(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_probe", lambda _: None)
    with pytest.raises(runtime.RuntimeSetupError, match="仅支持"):
        runtime.ensure_dependencies("python314", tmp_path)


def test_bootstrap_runtime_failure_precedes_token_or_install(monkeypatch):
    def missing():
        raise runtime.RuntimeSetupError("missing 3.12")

    def forbidden(*args):
        pytest.fail("runtime failure must not write credentials or install")

    monkeypatch.setattr(bootstrap, "_find_python", missing)
    monkeypatch.setattr(bootstrap, "_clear_proxy", lambda: None)
    monkeypatch.setattr(bootstrap, "write_env", forbidden)
    monkeypatch.setattr(bootstrap, "pip_install", forbidden)
    assert bootstrap.main(["--yes", "--skip-sync"]) == 1


def test_background_start_returns_after_health_without_waiting_on_server(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    for name in ("_clear_proxy", "_load_dotenv"):
        monkeypatch.setattr(easy_start, name, lambda: None)
    monkeypatch.setattr(easy_start, "_find_python", lambda: "verified312")
    monkeypatch.setattr(easy_start, "_ensure_env", lambda **_: False)
    monkeypatch.setattr(easy_start, "_pip_install", lambda _: None)
    monkeypatch.setattr(easy_start, "_port_in_use", lambda _: False)
    health = iter([False, True])
    monkeypatch.setattr(easy_start, "_wait_health", lambda **_: next(health))
    calls = []
    monkeypatch.setattr(easy_start, "_start_server", lambda py: calls.append(py) or object())
    assert easy_start.main(["--skip-sync", "--no-browser"]) == 0
    assert calls == ["verified312"]


def test_mplfinance_requirement_explicitly_accepts_verified_beta():
    requirements = [Requirement(line.split("#", 1)[0].strip())
                    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                    if line.split("#", 1)[0].strip()]
    requirement = next(r for r in requirements if r.name == "mplfinance")
    assert str(requirement.specifier) == "==0.12.10b0"
    assert requirement.specifier.contains("0.12.10b0")


def test_batch_is_ascii_and_git_enforces_windows_line_endings():
    (ROOT / "一键启动.bat").read_bytes().decode("ascii")
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text()


@pytest.mark.skipif(os.name != "nt", reason="Executes the actual Windows cmd.exe wrapper")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_actual_batch_preserves_exit_arguments_and_space_path(tmp_path, exit_code):
    root = tmp_path / "workspace with spaces & text"
    root.mkdir()
    created = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv312")],
                             capture_output=True, timeout=60, check=False)
    assert created.returncode == 0, created.stderr
    batch = root / "一键启动.bat"
    shutil.copyfile(ROOT / batch.name, batch)
    (root / "easy_start.py").write_text(
        "import sys\nprint('LAUNCH_FIXTURE', sys.argv[1:])\nraise SystemExit(" + str(exit_code) + ")\n",
        encoding="utf-8",
    )
    env = {**os.environ, "AB_START_NO_PAUSE": "1"}
    command = f'cmd.exe /d /s /c ""{batch}" --skip-sync --no-browser"'
    result = subprocess.run(command,
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=30, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == exit_code, output
    assert "LAUNCH_FIXTURE" in output
    assert "--skip-sync" in output and "--no-browser" in output
    assert "not recognized" not in output
    assert ("Startup failed" in output) == (exit_code != 0)
