"""查清哪些行缺 PIT 元数据（available_at / source）—— 只读，不改任何数据。

闸门 D 报「N 行缺元数据」时用这个定位：是哪张表、哪些交易日、什么时候进来的。
缺元数据的行无法判断「当时能不能看到」，直接拿去回测就是前视偏差，
所以先查清楚范围再决定怎么办，不要急着补。

    .venv312\\Scripts\\python.exe scripts\\diagnose_pit_metadata.py
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

TABLES = ("daily", "daily_basic", "moneyflow")
META_COLS = ("available_at", "source")


def default_db() -> Path:
    env = os.environ.get("AB_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "runtime" / "stock_data.db"


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    ap = argparse.ArgumentParser(description="定位缺 PIT 元数据的行")
    ap.add_argument("--db", default=str(default_db()))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"数据库不存在：{db}", file=sys.stderr)
        return 2
    if "lhb_product" in db.name:
        print("拒绝：这是龙虎榜副本。", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        print()
        print(f"行情库  {db}")
        print(f"体积    {db.stat().st_size / 1024**3:.2f} GB")

        grand_missing = 0
        for table in TABLES:
            cols = columns(conn, table)
            if not cols:
                print(f"\n{table}: 表不存在")
                continue
            present = [c for c in META_COLS if c in cols]
            print()
            print("=" * 66)
            print(f"{table}    元数据列：{present or '（一个都没有）'}")
            print("=" * 66)

            (total,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            (days,) = conn.execute(f"SELECT COUNT(DISTINCT trade_date) FROM {table}").fetchone()
            lo, hi = conn.execute(
                f"SELECT MIN(trade_date), MAX(trade_date) FROM {table}"
            ).fetchone()
            print(f"  总行数 {total:,}    交易日 {days:,}    范围 {lo} ~ {hi}")

            if not present:
                print("  （没有元数据列，无法按缺失统计）")
                continue

            where = " OR ".join(f"{c} IS NULL OR {c} = ''" for c in present)
            (missing,) = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
            grand_missing += missing
            pct = (missing / total * 100) if total else 0
            print(f"  缺元数据 {missing:,} 行（{pct:.1f}%）")

            if missing == 0:
                continue

            mlo, mhi = conn.execute(
                f"SELECT MIN(trade_date), MAX(trade_date) FROM {table} WHERE {where}"
            ).fetchone()
            print(f"  缺失区间 {mlo} ~ {mhi}")

            print("  按年份：")
            rows = conn.execute(
                f"SELECT substr(trade_date,1,4) AS y, COUNT(*), COUNT(DISTINCT trade_date)"
                f" FROM {table} WHERE {where} GROUP BY y ORDER BY y"
            ).fetchall()
            for year, cnt, dcount in rows:
                print(f"    {year}   {cnt:>10,} 行   {dcount:>4} 个交易日")

            # 有元数据的那部分覆盖到哪 —— 用来区分「历史回填没写元数据」
            # 和「新数据写入链路坏了」。前者缺失区间在早期，后者会摸到最新交易日。
            ok_lo, ok_hi = conn.execute(
                f"SELECT MIN(trade_date), MAX(trade_date) FROM {table}"
                f" WHERE NOT ({where})"
            ).fetchone()
            print(f"  有元数据的区间 {ok_lo} ~ {ok_hi}")

        # ---------------------------------------------------------------- 取证
        # 目的：在不知道「谁写的」时，先确定「什么时候写的、标了什么来源」。
        # rowid 在 SQLite 里近似插入顺序：如果历史交易日的 rowid 明显高于近期交易日，
        # 说明那批历史数据是后来才追加进去的，而不是一直就在库里。
        if "daily" in TABLES and columns(conn, "daily"):
            cols = columns(conn, "daily")
            print()
            print("=" * 66)
            print("取证：daily 表的 source 分布与插入顺序")
            print("=" * 66)

            if "source" in cols:
                print("  按 source 分组：")
                rows = conn.execute(
                    "SELECT COALESCE(NULLIF(source,''),'(空/NULL)') AS s, COUNT(*),"
                    " MIN(trade_date), MAX(trade_date), MIN(rowid), MAX(rowid)"
                    " FROM daily GROUP BY s ORDER BY COUNT(*) DESC"
                ).fetchall()
                for s, cnt, tlo, thi, rlo, rhi in rows:
                    print(f"    {s}")
                    print(f"        {cnt:>10,} 行   交易日 {tlo} ~ {thi}   rowid {rlo:,} ~ {rhi:,}")

            print()
            print("  rowid 与交易日的对应（每段取该年最小/最大 rowid）：")
            rows = conn.execute(
                "SELECT substr(trade_date,1,4) AS y, MIN(rowid), MAX(rowid), COUNT(*)"
                " FROM daily GROUP BY y ORDER BY y"
            ).fetchall()
            for year, rlo, rhi, cnt in rows:
                print(f"    {year}   rowid {rlo:>12,} ~ {rhi:>12,}   {cnt:>10,} 行")
            print()
            print("  读法：若 2015-2021 的 rowid 段整体高于 2023-2026，")
            print("        说明这批历史是最近才追加的；反之则是一直都在。")

        print()
        print("=" * 66)
        print(f"合计缺元数据 {grand_missing:,} 行")
        print()
        print("怎么读这份报告：")
        print("  · 缺失区间全在早期、且最新交易日有元数据 → 是一次历史回填没写 PIT 字段，")
        print("    当日链路是好的。修法是对那段区间补写元数据，或把它排除出 PIT 读路径。")
        print("  · 缺失一直摸到最新交易日 → 写入链路坏了，属于当下问题，要先停下来查。")
        print()
        print("无论哪种，都不要在没查清来源前直接 UPDATE 生产库补字段 ——")
        print("凭猜测填进去的 available_at 会让前视偏差变得不可检测。")
        print()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
