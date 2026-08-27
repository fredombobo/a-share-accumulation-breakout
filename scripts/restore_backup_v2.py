"""严格恢复演练：只接受带有效清单的备份，且绝不覆盖已有目标。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ab_screener.operations.backup import (
    BackupError,
    latest_backup,
    restore_verified_backup,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v2 verified backup restore drill")
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--restore-to")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    latest = latest_backup(args.backup_root)
    if latest is None:
        print("ERROR: no verified backup found", file=sys.stderr)
        return 1
    if args.dry_run:
        payload = {
            "status": "DRY_RUN",
            "source": latest["path"],
            "manifest": latest["manifest_path"],
            "archive_format": latest["archive_format"],
            "restore_to": args.restore_to,
            "checks": [
                "manifest_sha256",
                "archive_sha256",
                "PRAGMA integrity_check",
                "PRAGMA foreign_key_check",
                "logical_database_sha256",
            ],
            "overwrite": False,
        }
    else:
        if not args.restore_to:
            print("ERROR: --restore-to is required for actual restore", file=sys.stderr)
            return 2
        try:
            payload = restore_verified_backup(latest["path"], args.restore_to)
        except BackupError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.report:
        report = Path(args.report).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
