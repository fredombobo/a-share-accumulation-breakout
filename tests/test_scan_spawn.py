"""G1：扫描子进程封装不把 subprocess 暴露给 API。"""
from __future__ import annotations

from pathlib import Path

from ab_screener.application.scan_spawn import write_scan_cancel_flag


def test_write_scan_cancel_flag(tmp_path: Path):
    write_scan_cancel_flag("abc123", runtime_dir=tmp_path)
    flag = tmp_path / "scan_abc123.cancel"
    assert flag.read_text(encoding="utf-8") == "1"
