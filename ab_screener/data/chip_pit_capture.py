"""筹码分布（cyq_perf）前向 PIT 捕获。

背景（docs/PHASE0-2-PROGRESS-2026-09-15.md 第 3 节第 1 步）：
`cyq_history` 里的历史记录是 2026-08 批量入库，`available_at` 是入库时刻，
按预登记口径（`available_at ≤ 次一交易日 09:15`）可用事件为 0。
不能倒填历史可用时点，唯一出路是从现在起每个交易日收盘后抓取当日快照，
并把**真实抓取完成时刻**记为 `available_at`。

契约：
- 只前向：每次只抓一个交易日（默认库内 `daily` 最新交易日），不补历史；
  迟到的抓取照样如实记录时间，但不会让它对更早的决策可用。
- append-only：按 (ts_code, trade_date) 与最新 revision 的 content_hash 比较，
  只追加新增或内容变化的行；同内容重跑为 NOOP，不产生重复 revision。
- 分页：网关单页上限按 `page_size` 处理；整页返回时继续翻页。若翻页返回
  已见过的键（网关忽略 offset）或超过页数上限，拒绝写入，绝不静默截断。
- 覆盖率：以同日 `daily` 分区的股票集合为分母；低于阈值记 PARTIAL
  （已抓到的行照样写入，下次重跑只补缺失行）。
- 不迁移：缺 `cyq_history` / `raw_ingest_manifests` / `daily` 时拒绝执行。
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ab_screener.domain.data_point import canonical_json, content_hash_for, normalize_ts

_TZ = ZoneInfo("Asia/Shanghai")

DATASET = "cyq"
TABLE = "cyq_history"
SOURCE = "tushare_cyq_daily"
KEY_COLUMNS = ("ts_code", "trade_date")
PAGE_SIZE = 5000
MAX_PAGES = 4
MIN_COVERAGE = 0.95
# 预登记口径：筹码值必须在次一交易日开盘前可用
ENTRY_CUTOFF = "09:15:00"


class ChipCaptureError(RuntimeError):
    """Fail-closed 捕获错误：不写入任何行。"""


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _rows_from_frame(frame: pd.DataFrame | None, trade_date: str) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    missing = [c for c in KEY_COLUMNS if c not in frame.columns]
    if missing:
        raise ChipCaptureError(f"cyq_perf 返回缺少业务键列: {missing}")
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        code = str(record.get("ts_code") or "").strip()
        day = str(record.get("trade_date") or "").strip()
        if not code or not day:
            raise ChipCaptureError(f"cyq_perf 返回行业务键缺失: {record}")
        if day != trade_date:
            raise ChipCaptureError(f"cyq_perf 返回了非目标日期 {day}（目标 {trade_date}）")
        payload = {k: _clean(v) for k, v in record.items() if k not in KEY_COLUMNS}
        rows.append({"ts_code": code, "trade_date": day, "payload": payload})
    return rows


def fetch_cyq_partition(
    provider: Any,
    trade_date: str,
    *,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> tuple[list[dict[str, Any]], int]:
    """分页拉取单个交易日的全部 cyq_perf 行，返回 (rows, pages)。"""
    if page_size < 1 or max_pages < 1:
        raise ValueError("page_size/max_pages 必须为正整数")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        frame = provider.cyq_perf(trade_date=trade_date, offset=page * page_size, limit=page_size)
        page_rows = _rows_from_frame(frame, trade_date)
        codes = [r["ts_code"] for r in page_rows]
        if len(set(codes)) != len(codes):
            raise ChipCaptureError(f"cyq_perf 单页出现重复股票（{trade_date} 第 {page + 1} 页）")
        if page_rows and seen.issuperset(codes):
            raise ChipCaptureError("数据网关忽略分页 offset；拒绝写入可能被截断的筹码快照")
        overlap = seen.intersection(codes)
        if overlap:
            raise ChipCaptureError(f"cyq_perf 翻页结果重叠 {len(overlap)} 只股票；拒绝写入")
        seen.update(codes)
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            return rows, page + 1
    raise ChipCaptureError(
        f"cyq_perf 连续 {max_pages} 页均为满页（{max_pages * page_size} 行）；"
        "可能仍有未取回数据，拒绝写入"
    )


def _require_tables(conn: sqlite3.Connection) -> None:
    tables = {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    missing = sorted({TABLE, "raw_ingest_manifests", "daily"} - tables)
    if missing:
        raise ChipCaptureError(f"数据库缺少表 {missing}；本命令不做迁移，拒绝执行")


def _ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA query_only=1")
    return conn


def _latest_daily_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    return str(row[0]) if row and row[0] else None


def _expected_codes(conn: sqlite3.Connection, trade_date: str) -> set[str]:
    """覆盖率分母：同日有行情的沪深股票（北交所是否在 cyq_perf 范围内未确认，单独报告）。"""
    rows = conn.execute("SELECT DISTINCT ts_code FROM daily WHERE trade_date=?", (trade_date,)).fetchall()
    return {str(r[0]) for r in rows if str(r[0]).endswith((".SH", ".SZ"))}


def _latest_revisions(
    conn: sqlite3.Connection, trade_date: str, codes: list[str]
) -> dict[str, tuple[int, str]]:
    """按 (ts_code, trade_date) 主键前缀逐批查最新 revision；避免按日期全表扫描。"""
    latest: dict[str, tuple[int, str]] = {}
    for start in range(0, len(codes), 500):
        chunk = codes[start:start + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT ts_code, revision, content_hash FROM {TABLE}"
            f" WHERE ts_code IN ({marks}) AND trade_date=? ORDER BY ts_code, revision",
            (*chunk, trade_date),
        ).fetchall()
        for code, revision, digest in rows:
            latest[str(code)] = (int(revision), str(digest))
    return latest


def _manifest_rows(conn: sqlite3.Connection, trade_date: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(row_count), 0) FROM raw_ingest_manifests"
        " WHERE dataset=? AND partition_key=?",
        (DATASET, trade_date),
    ).fetchone()
    return int(row[0] or 0)


def _session_closed(trade_date: str, now: datetime) -> bool:
    close = datetime.strptime(trade_date, "%Y%m%d").replace(hour=15, tzinfo=_TZ)
    return now >= close


def capture_chip_snapshot(
    db_path: str | Path,
    provider: Any | None,
    *,
    trade_date: str | None = None,
    apply: bool = False,
    min_coverage: float = MIN_COVERAGE,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """抓取单个交易日筹码快照并按 PIT 追加写入；apply=False 只出计划。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ChipCaptureError(f"数据库不存在: {path}")
    now_fn = clock or (lambda: datetime.now(_TZ))
    if trade_date is not None and not (len(trade_date) == 8 and trade_date.isdigit()):
        raise ChipCaptureError(f"trade_date 必须是 YYYYMMDD: {trade_date!r}")

    with closing(_ro_connect(path)) as conn:
        _require_tables(conn)
        target = trade_date or _latest_daily_date(conn)
        if not target:
            raise ChipCaptureError("daily 表为空，无法确定目标交易日")
        expected = _expected_codes(conn, target)
        existing_rows = _manifest_rows(conn, target)

    result: dict[str, Any] = {
        "dataset": DATASET,
        "trade_date": target,
        "source": SOURCE,
        "expected_codes": len(expected),
        "existing_manifest_rows": existing_rows,
    }
    if not expected:
        raise ChipCaptureError(f"daily 缺少 {target} 分区；先完成行情同步再抓筹码")
    if not _session_closed(target, now_fn()):
        raise ChipCaptureError(f"{target} 尚未收盘，拒绝抓取盘中筹码快照")
    if not apply:
        result["status"] = "PLANNED"
        return result
    if provider is None:
        raise ChipCaptureError("apply 需要数据源 provider")

    rows, pages = fetch_cyq_partition(provider, target, page_size=page_size, max_pages=max_pages)
    # 可用时点取抓取完成之后的时刻：只会比真实可用更晚（保守），绝不更早。
    available_at = normalize_ts(now_fn())
    captured = {r["ts_code"] for r in rows}
    coverage = len(captured & expected) / len(expected)
    result.update(
        {
            "pages": pages,
            "fetched_rows": len(rows),
            "coverage": round(coverage, 6),
            "missing_codes": len(expected - captured),
            "bj_rows": sum(1 for code in captured if code.endswith(".BJ")),
            "available_at": available_at,
        }
    )

    ingested_at = datetime.now(_TZ).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(path), timeout=60)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 在写锁内重读最新 revision，防并发写入分配重复 revision
        latest = _latest_revisions(conn, target, sorted({r["ts_code"] for r in rows}))
        appended: list[tuple[Any, ...]] = []
        payloads: list[str] = []
        revised = 0
        for row in sorted(rows, key=lambda r: r["ts_code"]):
            digest = content_hash_for(row["payload"])
            state = latest.get(row["ts_code"])
            if state is not None and state[1] == digest:
                continue
            if state is not None:
                revised += 1
            payload_json = canonical_json(row["payload"])
            payloads.append(payload_json)
            appended.append(
                (
                    row["ts_code"],
                    target,
                    (state[0] + 1) if state else 1,
                    available_at,
                    SOURCE,
                    digest,
                    payload_json,
                )
            )
        manifest_id = None
        if appended:
            conn.executemany(
                f"INSERT INTO {TABLE} (ts_code, trade_date, revision, available_at, source,"
                " content_hash, payload_json) VALUES (?,?,?,?,?,?,?)",
                appended,
            )
            content_sha = hashlib.sha256("\n".join(payloads).encode("utf-8")).hexdigest()
            manifest_id = hashlib.sha256(
                f"{DATASET}|{target}|{available_at}|{SOURCE}|{content_sha}".encode()
            ).hexdigest()[:16]
            conn.execute(
                "INSERT INTO raw_ingest_manifests (manifest_id, dataset, partition_key, source,"
                " available_at, row_count, content_sha256, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
                (manifest_id, DATASET, target, SOURCE, available_at, len(appended), content_sha, ingested_at),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not rows:
        status = "EMPTY"
    elif coverage < min_coverage:
        status = "PARTIAL"
    elif appended:
        status = "COMPLETED"
    else:
        status = "NOOP"
    result.update(
        {
            "status": status,
            "appended_rows": len(appended),
            "revised_rows": revised,
            "unchanged_rows": len(rows) - len(appended),
            "manifest_id": manifest_id,
        }
    )
    return result


def _entry_cutoff(trade_date: str, open_days: list[str]) -> tuple[datetime, bool]:
    """次一交易日 09:15；日历未覆盖时退回次一自然日 09:15（更早，因而更保守）。"""
    later = [d for d in open_days if d > trade_date]
    if later:
        day = datetime.strptime(later[0], "%Y%m%d").date()
        exact = True
    else:
        day = datetime.strptime(trade_date, "%Y%m%d").date() + timedelta(days=1)
        exact = False
    hh, mm, ss = (int(x) for x in ENTRY_CUTOFF.split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=_TZ), exact


def chip_pit_readiness(db_path: str | Path, *, since: str | None = None) -> dict[str, Any]:
    """只读：按入库清单统计哪些交易日的筹码值在次一交易日开盘前真实可用。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ChipCaptureError(f"数据库不存在: {path}")
    with closing(_ro_connect(path)) as conn:
        _require_tables(conn)
        params: list[Any] = [DATASET]
        clause = ""
        if since:
            clause = " AND partition_key>=?"
            params.append(since)
        manifests = conn.execute(
            "SELECT partition_key, available_at, row_count FROM raw_ingest_manifests"
            " WHERE dataset=?" + clause + " ORDER BY partition_key, available_at",
            params,
        ).fetchall()
        try:
            open_days = [
                str(r[0])
                for r in conn.execute(
                    "SELECT cal_date FROM trade_cal WHERE is_open=1 ORDER BY cal_date"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            open_days = []

    by_date: dict[str, list[tuple[datetime, int]]] = {}
    for key, available, count in manifests:
        stamp = datetime.fromisoformat(normalize_ts(str(available)))
        by_date.setdefault(str(key), []).append((stamp, int(count or 0)))

    usable: list[dict[str, Any]] = []
    late: list[str] = []
    for day in sorted(by_date):
        cutoff, exact = _entry_cutoff(day, open_days)
        in_time = sum(n for stamp, n in by_date[day] if stamp <= cutoff)
        if in_time > 0:
            usable.append(
                {
                    "trade_date": day,
                    "rows_before_entry": in_time,
                    "entry_cutoff": cutoff.isoformat(timespec="seconds"),
                    "calendar_exact": exact,
                }
            )
        else:
            late.append(day)
    return {
        "dataset": DATASET,
        "rule": f"available_at <= 次一交易日 {ENTRY_CUTOFF}（Asia/Shanghai）",
        "partitions_with_manifest": len(by_date),
        "strict_usable_dates": len(usable),
        "first_usable": usable[0]["trade_date"] if usable else None,
        "latest_usable": usable[-1]["trade_date"] if usable else None,
        "late_only_dates": len(late),
        "usable": usable,
        "checked_at": datetime.now(_TZ).isoformat(timespec="seconds"),
    }
