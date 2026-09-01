"""以前台进程启动隔离龙虎榜产品副本（供验收/高级用户）。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.application.pit_backfill import assert_copy_database
from ab_screener.data.migration_intents import register_lhb_intents
from ab_screener.data.schema_check import assert_schema_compatible

# 8123 服务的是龙虎榜副本，schema 断言必须把 LHB 迁移算进来。
register_lhb_intents()


def main() -> int:
    parser = argparse.ArgumentParser(description="启动龙虎榜产品副本 Web")
    parser.add_argument("--db", required=True, help="绝对路径数据库副本")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()
    db = assert_copy_database(Path(args.db), maintenance_authorized=False)
    assert_schema_compatible(db)
    os.environ["AB_DB_PATH"] = str(db)
    os.environ["AB_BACKEND_PORT"] = str(args.port)
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    os.environ["V2_PIT_READ_ENABLED"] = "false"
    os.environ["DAILY_SCHEDULER_ENABLED"] = "false"

    import uvicorn

    from web.backend_app import app

    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
