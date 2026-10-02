"""独立扫描 Worker：顺序领取 scan_jobs，调 scan_job_runner 子进程。

用法：
  python -m ab_screener.jobs.scan_worker
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ab_screener.application.scan_jobs import (
    CANCELLED,
    FAILED,
    ScanJobStore,
)
from scan_runtime import kill_process_tree

WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"
POLL_SEC = 0.5
CANCEL_POLL_SEC = 0.25


def run_forever(db_path: Path | None = None) -> None:
    store = ScanJobStore(db_path or (_ROOT / "runtime" / "stock_data.db"))
    runtime = _ROOT / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    print(f"[scan_worker] start id={WORKER_ID}", flush=True)
    while True:
        job = store.claim_next(WORKER_ID)
        if not job:
            time.sleep(POLL_SEC)
            continue
        tid = job["task_id"]
        top = int(job.get("top_n") or 20)
        days = int(job.get("days") or 160)
        print(f"[scan_worker] claim {tid} top={top} days={days}", flush=True)
        progress = runtime / f"scan_{tid}.progress.json"
        result = runtime / f"scan_{tid}.result.json"
        cancel_f = runtime / f"scan_{tid}.cancel"
        profile_path = runtime / f"scan_{tid}.profile.json"
        if not profile_path.is_file():
            store.finish(tid, status=FAILED, error_code='PROFILE_MISSING', error_message='任务缺少冻结参数文件，请重新提交扫描')
            continue
        for p in (progress, result, cancel_f):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        cmd = [
            sys.executable,
            str(_ROOT / "scan_job_runner.py"),
            "--task-id", tid,
            "--top", str(top),
            "--days", str(days),
            "--progress", str(progress),
            "--result", str(result),
            "--cancel-file", str(cancel_f),
            "--profile", str(profile_path),
            "--db", str(store.db_path),
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd, cwd=str(_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            while True:
                if store.is_cancel_requested(tid):
                    cancel_f.write_text("1", encoding="utf-8")
                    kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=3)
                    except Exception:  # noqa: BLE001
                        kill_process_tree(proc.pid)
                    store.finish(tid, status=CANCELLED, error_code="CANCELLED", error_message="user cancel")
                    print(f"[scan_worker] cancelled {tid}", flush=True)
                    break
                store.heartbeat(tid, _read_progress(progress))
                rc = proc.poll()
                if rc is not None:
                    data = _read_result(result)
                    if data.get("cancelled") or data.get("status") == "cancelled":
                        store.finish(tid, status=CANCELLED)
                    elif data.get("status") == "error" or rc != 0:
                        store.finish(
                            tid, status=FAILED,
                            error_code="SCAN_FAILED",
                            error_message=str(data.get("error") or f"exit={rc}")[:500],
                        )
                    else:
                        from ab_screener.application.scan_audit import complete_scan_run
                        from ab_screener.domain.profile import load_profile_json
                        from build_version import build_version
                        profile = load_profile_json(profile_path)
                        if 'scan_candidates' not in data or data.get('strategy_profile_hash') != profile.config_hash():
                            raise ValueError('scan payload or frozen profile verification failed')
                        complete_scan_run(
                            store.db_path, run_id=tid, task_id=tid, as_of=data['latest_date'],
                            days=int(data.get('effective_days') or days), result=data,
                            count_a=int(data['count_a']), count_b=int(data['count_b']),
                            strategy_snapshot=profile.to_canonical_dict(), config_hash=profile.config_hash(),
                            code_version=build_version(), research_mode='daily_research',
                        )
                        # 固化进度结果到 checkpoint 供 API 读取
                        store.heartbeat(tid, {**_read_progress(progress), "result": data})
                    print(f"[scan_worker] done {tid} rc={rc}", flush=True)
                    break
                time.sleep(CANCEL_POLL_SEC)
        except Exception as e:  # noqa: BLE001
            kill_process_tree(proc.pid)
            store.finish(tid, status=FAILED, error_code="WORKER_ERROR", error_message=str(e)[:500])
            print(f"[scan_worker] error {tid}: {e}", flush=True)


def _read_progress(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _read_result(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


if __name__ == "__main__":
    run_forever()
