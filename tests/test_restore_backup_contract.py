"""restore_backup.ps1 契约：DryRun 在无交互终端 exit 0 且打印解析后的源/目标。"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path


def test_restore_backup_dryrun_exits_zero(tmp_path: Path) -> None:
    """DryRun 不实际恢复，必须 exit 0 且打印 DRY-RUN 与源/目标。"""
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    fake_backup = backup_root / "backup_20260822_000000.db"
    sqlite3.connect(fake_backup).close()

    ps1 = (Path("scripts") / "restore_backup.ps1").resolve()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
         "-BackupRoot", str(backup_root), "-RestoreTo", str(tmp_path / "restore.db"), "-DryRun"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out
    assert "DRY-RUN" in out
    assert str(fake_backup.name) in out


def test_restore_backup_dryrun_without_restoreto_exits_zero(tmp_path: Path) -> None:
    """DryRun 不传 RestoreTo 也必须成功（预览源备份，不要求目标）。"""
    backup_root = tmp_path / "backups2"
    backup_root.mkdir()
    fake_backup = backup_root / "backup_20260822_000000.db"
    sqlite3.connect(fake_backup).close()

    ps1 = (Path("scripts") / "restore_backup.ps1").resolve()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
         "-BackupRoot", str(backup_root), "-DryRun"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out
    assert "DRY-RUN" in out
    assert str(fake_backup.name) in out
