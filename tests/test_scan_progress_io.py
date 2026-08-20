"""Windows scan progress-file regression tests."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ab_screener.application.scan_jobs import FAILED, ScanJobStore
from local_store import LocalStore
from scan_job_runner import _write_json


def test_progress_write_waits_for_a_windows_reader_to_release(tmp_path: Path) -> None:
    """Backend polling must not make the scan subprocess fail on WinError 5."""
    progress = tmp_path / "scan_task.progress.json"
    progress.write_text('{"progress": 1}', encoding="utf-8")

    reader = progress.open("r", encoding="utf-8")
    release = threading.Timer(0.05, reader.close)
    release.start()
    try:
        _write_json(progress, {"progress": 100, "stage": "完成"})
    finally:
        reader.close()
        release.join(timeout=1)

    assert json.loads(progress.read_text(encoding="utf-8")) == {
        "progress": 100,
        "stage": "完成",
    }


def test_subprocess_error_is_persisted_as_a_terminal_scan_job(tmp_path: Path) -> None:
    """A failed child process must not leave scan_jobs stuck at RUNNING."""
    from web.backend_app import _finish_persisted_scan_failure

    db_path = tmp_path / "scan.db"
    LocalStore(db_path)
    store = ScanJobStore(db_path)
    store.upsert_running("task-error", top_n=20, days=160)

    changed = _finish_persisted_scan_failure(
        "task-error",
        "child failed",
        db_path=db_path,
    )

    job = store.get("task-error")
    assert changed is True
    assert job is not None
    assert job["status"] == FAILED
    assert job["error_code"] == "SCAN_FAILED"
    assert job["error_message"] == "child failed"
