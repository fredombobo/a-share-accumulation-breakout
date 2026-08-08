"""scan_result 信号字段回填（v2）

阶段0 性能修复的一部分：把当前 scan_result 缺失的 box_high/box_low/ma5/ma20
一次性计算并写回数据库，此后 /api/overview 直接读表、零重算。

用法：python backfill_scan_signals.py
"""
from __future__ import annotations

import os
import sys
import time

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from local_store import LocalStore
from signals import detect_accumulation_breakout


def main() -> int:
    store = LocalStore()
    # 与 upsert_scan_result 一致的增量列迁移（老库缺 4 列）
    with store._connect() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(scan_result)").fetchall()}
        for col in ("box_high", "box_low", "ma5", "ma20"):
            if col not in have:
                conn.execute(f"ALTER TABLE scan_result ADD COLUMN {col} REAL")
        # 取全部需要回填的行（所有交易日，不只最新）
        rows = conn.execute(
            "SELECT trade_date, ts_code FROM scan_result WHERE box_high IS NULL"
        ).fetchall()
    if not rows:
        print("scan_result 全部已含信号字段，无需回填")
        return 0

    print(f"回填 {len(rows)} 行（跨 {len({r[0] for r in rows})} 个交易日）…")
    codes = sorted({r[1] for r in rows})
    daily = store.load_daily(ts_codes=codes)
    grp = daily.groupby("ts_code", sort=False)
    sig_cache: dict[str, dict] = {}
    t0 = time.time()
    with store._connect() as conn:
        n = 0
        for trade_date, code in rows:
            sig = sig_cache.get(code)
            if sig is None:
                try:
                    g = grp.get_group(code).sort_values("trade_date").copy()
                except KeyError:
                    sig = {}
                else:
                    g["date"] = pd.to_datetime(g["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
                    sig = detect_accumulation_breakout(g)
                sig_cache[code] = sig
            conn.execute(
                "UPDATE scan_result SET box_high=?, box_low=?, ma5=?, ma20=? WHERE trade_date=? AND ts_code=?",
                (
                    sig.get("box_high"), sig.get("box_low"), sig.get("ma5"), sig.get("ma20"),
                    trade_date, code,
                ),
            )
            n += 1
    # 验证
    with store._connect() as conn:
        still_null = conn.execute(
            "SELECT COUNT(*) FROM scan_result WHERE box_high IS NULL"
        ).fetchone()[0]
    print(f"回填完成：{n} 行，耗时 {time.time()-t0:.1f}s，剩余 null={still_null}")
    return 0 if still_null == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
