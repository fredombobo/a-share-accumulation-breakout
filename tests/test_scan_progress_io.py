"""Windows scan progress-file regression tests."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd

import scan_job_runner
from ab_screener.application.scan_jobs import FAILED, ScanJobStore, to_api_status
from local_store import LocalStore
from scan_job_runner import _candidate_codes, _configure_console_encoding, _write_json


class _FakeStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def test_scan_runner_forces_utf8_before_emoji_logging(monkeypatch) -> None:
    stdout = _FakeStream()
    stderr = _FakeStream()
    monkeypatch.setattr(scan_job_runner.sys, "stdout", stdout)
    monkeypatch.setattr(scan_job_runner.sys, "stderr", stderr)

    _configure_console_encoding()

    expected = [{"encoding": "utf-8", "errors": "backslashreplace"}]
    assert stdout.calls == expected
    assert stderr.calls == expected


def test_scan_runner_preserves_bounded_candidate_codes_for_shadow_hook() -> None:
    result = {
        "hits": ["000001.SZ", {"ts_code": "600000.SH"}],
        "df_a": pd.DataFrame([{"ts_code": "000001.SZ"}]),
        "df_b": pd.DataFrame([{"ts_code": "300001.SZ"}]),
    }

    assert _candidate_codes(result) == ["000001.SZ", "600000.SH", "300001.SZ"]


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
    from ab_screener.api.routers.legacy_scan import _finish_persisted_scan_failure

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


def test_scan_failure_redacts_vendor_echo_before_db_and_api(tmp_path: Path) -> None:
    credential = "T" * 48
    db_path = tmp_path / "scan-redaction.db"
    LocalStore(db_path)
    store = ScanJobStore(db_path)
    store.upsert_running("task-secret", top_n=20, days=160)

    changed = store.finish(
        "task-secret",
        status=FAILED,
        error_code="SCAN_FAILED",
        error_message=f"token不对，您传过来的是{credential}请确认",
    )

    job = store.get("task-secret")
    assert changed is True
    assert job is not None
    assert credential not in str(job["error_message"])
    assert "[REDACTED]" in str(job["error_message"])
    assert credential not in str(to_api_status(job)["error"])


def test_scan_api_status_exposes_lifecycle_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "scan-timestamps.db"
    LocalStore(db_path)
    store = ScanJobStore(db_path)
    store.upsert_running("task-clock", top_n=20, days=160)
    store.heartbeat("task-clock", {"stage": "预筛", "progress": 17})

    job = store.get("task-clock")
    assert job is not None
    status = to_api_status(job)

    assert status["started_at"] == job["started_at"]
    assert status["updated_at"] == job["updated_at"]
    assert status["heartbeat_at"] == job["heartbeat_at"]
    assert status["stage"] == "预筛"
    assert status["progress"] == 17
