"""G1：扫描子进程封装不把 subprocess 暴露给 API。"""
from __future__ import annotations

from pathlib import Path

from ab_screener.application import scan_spawn
from ab_screener.application.scan_spawn import spawn_scan_runner, write_scan_cancel_flag


def test_write_scan_cancel_flag(tmp_path: Path):
    write_scan_cancel_flag("abc123", runtime_dir=tmp_path)
    flag = tmp_path / "scan_abc123.cancel"
    assert flag.read_text(encoding="utf-8") == "1"


def test_spawn_passes_frozen_profile_file_to_child(tmp_path: Path, monkeypatch):
    captured: list[str] = []

    class FakeProcess:
        pid = 123

    def fake_popen(command, **_kwargs):
        captured.extend(str(value) for value in command)
        return FakeProcess()

    monkeypatch.setattr(scan_spawn.subprocess, "Popen", fake_popen)
    profile = tmp_path / "scan.profile.json"
    profile.write_text("{}", encoding="utf-8")

    spawn_scan_runner(
        task_id="abc123",
        top=20,
        days=160,
        progress=tmp_path / "progress.json",
        result=tmp_path / "result.json",
        cancel_file=tmp_path / "cancel",
        profile=profile,
        cwd=tmp_path,
    )

    index = captured.index("--profile")
    assert captured[index + 1] == str(profile)
