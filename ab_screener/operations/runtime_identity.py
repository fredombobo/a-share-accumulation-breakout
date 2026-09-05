"""Read-only AB service verification, independent of Windows venv forwarding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, build_opener


def verify_identity(payload: dict[str, Any], root: str, database: str, build: str) -> None:
    expected = {"product": "accumulation_breakout", "port": 8001,
                "build_version": build, "live_trading_enabled": False}
    for key, value in expected.items():
        if type(payload.get(key)) is not type(value) or payload.get(key) != value:
            raise ValueError(f"服务身份不符: {key}；请用权威目录重启 8001")
    for key, value in (("repository_root", root), ("database_path", database)):
        actual = payload.get(key)
        if not isinstance(actual, str) or Path(actual).resolve() != Path(value).resolve():
            raise ValueError(f"服务身份不符: {key}；拒绝扫描其它产品/数据库")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    from build_version import build_version

    try:
        with build_opener(ProxyHandler({})).open(
            "http://127.0.0.1:8001/api/health", timeout=30,
        ) as response:
            payload = json.load(response)
        verify_identity(payload, args.root, args.db, build_version())
    except (OSError, ValueError) as exc:
        print(f"IDENTITY_REJECTED: {exc}")
        return 1
    print(f"IDENTITY_OK accumulation_breakout :8001 build={payload['build_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
