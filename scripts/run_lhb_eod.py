"""在显式数据库副本运行龙虎榜盘后流水线。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.application.lhb_product import run_lhb_product_day
from ab_screener.data.migration_intents import register_lhb_intents
from ab_screener.data.schema_check import assert_schema_compatible

register_lhb_intents()


def main() -> int:
    parser = argparse.ArgumentParser(description="龙虎榜盘后流水线（副本 / research-only）")
    parser.add_argument("--db", required=True, help="绝对路径数据库副本")
    parser.add_argument("--trade-date", required=True, help="YYYYMMDD")
    parser.add_argument(
        "--confirm-published",
        action="store_true",
        help="确认该交易日数据已发布；空响应才可记 VALID_EMPTY",
    )
    args = parser.parse_args()
    db = Path(args.db)
    assert_schema_compatible(db)
    result = run_lhb_product_day(
        db,
        args.trade_date,
        published=args.confirm_published,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
