"""离线深检：PRAGMA integrity_check + 产出 JSON 完整性证书。

快速健康接口不得在热路径跑 integrity_check（16GB 库需数分钟），
深检由本脚本离线执行并把证书写到 runtime/v2/integrity_report.json，
接口只读取匹配当前 DB fingerprint 的最新证书。

用法:
    python scripts/check_db_integrity.py --db runtime/stock_data.db
    python scripts/check_db_integrity.py --db runtime/stock_data.db --out runtime/v2/integrity_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def db_fingerprint(path: Path) -> str:
    """轻量 DB 指纹：路径名 + size + mtime（与快速健康接口同口径）。"""
    st = path.stat()
    return hashlib.sha256(f"{path.name}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="离线 PRAGMA integrity_check 深检")
    parser.add_argument("--db", required=True, help="数据库绝对路径")
    parser.add_argument("--out", default="runtime/v2/integrity_report.json",
                        help="证书输出路径（默认 runtime/v2/integrity_report.json）")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.is_file():
        print(f"DB 不存在: {db}", file=sys.stderr)
        return 2

    started = datetime.now(UTC).isoformat(timespec="seconds")
    t0 = time.time()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        conn.close()
    elapsed = round(time.time() - t0, 1)
    finished = datetime.now(UTC).isoformat(timespec="seconds")

    report = {
        "db": str(db),
        "fingerprint": db_fingerprint(db),
        "started_at": started,
        "finished_at": finished,
        "duration_sec": elapsed,
        "integrity": integrity,
        "tables": tables,
        "sha256": _sha256(db),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
