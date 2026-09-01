"""检查某个交易日的行情数据是否已经齐全 —— 跑闸门 D 之前的前置检查。

闸门 D 校的是「库里最新交易日分区」，数据没同步完就跑，必然 FAIL 在
moneyflow 上，白等几分钟。先用这个脚本确认，省掉那次空跑。

    .venv312\\Scripts\\python.exe scripts\\check_data_ready.py
    .venv312\\Scripts\\python.exe scripts\\check_data_ready.py --date 20260901

退出码：0 = 三张表都有数据，可以跑门禁；1 = 还缺，继续等。
只读打开数据库，不写任何东西。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DATASETS = ("daily", "daily_basic", "moneyflow")


def default_db() -> Path:
    env = os.environ.get("AB_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "runtime" / "stock_data.db"


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="检查交易日数据是否齐全")
    ap.add_argument("--db", default=str(default_db()), help="行情库路径")
    ap.add_argument("--date", default="", help="交易日 yyyymmdd，默认取 daily 表里的最大值")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"数据库不存在：{db}", file=sys.stderr)
        return 2
    if "lhb_product" in db.name:
        print("拒绝：这是龙虎榜副本，不是生产行情库。", file=sys.stderr)
        return 2

    # 只读挂载：这个脚本永远不该写生产库
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        if not table_exists(conn, "daily"):
            print("库里没有 daily 表，路径可能不对。", file=sys.stderr)
            return 2

        date = args.date.strip()
        if not date:
            row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
            date = (row[0] or "") if row else ""
            if not date:
                print("daily 表是空的。", file=sys.stderr)
                return 2

        print("")
        print(f"行情库  {db}")
        print(f"交易日  {date}")
        print("")

        ready = True
        for name in DATASETS:
            if not table_exists(conn, name):
                print(f"  {name:<12} 表不存在")
                ready = False
                continue
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {name} WHERE trade_date = ?", (date,)
            ).fetchone()
            mark = "OK  " if count > 0 else "缺  "
            print(f"  {name:<12} {mark}{count:>6} 行")
            if count == 0:
                ready = False

        print("")
        if ready:
            print("三张表都有数据，可以跑闸门 D：")
            print("    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\run_real_data_gate.ps1")
        else:
            print("数据还不齐 —— 现在跑闸门 D 一定 FAIL，先等同步。")
            print("Tushare 的 moneyflow 发布时间会飘，计划任务已配 45 分钟 x3 重试。")
        print("")
        return 0 if ready else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
