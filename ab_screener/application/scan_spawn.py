"""扫描子进程拉起（application 层；API 禁止 import subprocess）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ScanChild:
    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def poll(self) -> int | None:
        return self._proc.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:  # noqa: BLE001
            pass


def spawn_scan_runner(
    *,
    task_id: str,
    top: int,
    days: int,
    progress: Path,
    result: Path,
    cancel_file: Path,
    profile: Path,
    cwd: Path | None = None,
) -> ScanChild:
    root = cwd or ROOT
    cmd = [
        sys.executable,
        str(root / "scan_job_runner.py"),
        "--task-id", task_id,
        "--top", str(top),
        "--days", str(days),
        "--progress", str(progress),
        "--result", str(result),
        "--cancel-file", str(cancel_file),
        "--profile", str(profile),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return ScanChild(proc)


def write_scan_cancel_flag(task_id: str, *, runtime_dir: Path | None = None) -> None:
    root = runtime_dir or (ROOT / "runtime")
    path = root / f"scan_{task_id}.cancel"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
    except OSError:
        pass
