"""PIT 回填编排：按数据集/分区键分块，checkpoint 断点续跑。

契约（implementation P1.1 / V2R-D）：
- 分块：每个 (dataset, partition_key) 一块，单块行数 ≤ MAX_ROWS_PER_TX
  （默认 5 万）。写前登记 checkpoint(in_progress)，成功后置 done。
- 中断恢复：done 且 source_hash 一致的分区跳过；in_progress 续跑。
- 按分区逐块拉取（daily 族按 trade_date、fina/holder 按 ts_code、stock_basic 单块），
  避免一次性载入全量历史的内存峰值。
- 公司行为同步（CorporateActionBackfill）：按 ts_code 分区，每条记录携带
  effective_at / available_at / ingested_at / source / revision；checkpoint 记录
  最后完成分区，不允许部分分区被标记完成（全部成功才置 done）。
- 覆盖率与抽样 hash 100% 通过后才允许翻转 V2_PIT_READ_ENABLED；
  本模块提供 coverage_report() 供门禁使用。
- 时间统一 +08:00；写入一律走 pit_writer（append-only）。
- 镜像网关约束：fina_indicator 必须按 ts_code 拉取（不支持纯 period/日期范围），
  因此 fina_indicator 分区键 = 标的全集，拉全历史报告期（量小，全量存储）。
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.adapters.tushare_pit import (
    df_to_pit_rows,
    fetch_corporate_actions,
    fetch_pit_rows,
    get_pro_handle,
)
from ab_screener.data.corporate_action_repository import CorporateActionError, CorporateActionRepository
# HISTORY_TABLES 取 pit_writer：它在 aux_history 基础上并入了 LHB_PIT_HISTORY_TABLES，
# 直接用 aux_history_v2.ALL_HISTORY_TABLES 会让 top_inst 等龙虎榜分区被判为未知数据集。
from ab_screener.data.pit_writer import HISTORY_TABLES, MAX_ROWS_PER_TX, build_records, write_chunk
from ab_screener.domain.data_point import normalize_ts

_TZ = ZoneInfo("Asia/Shanghai")
SOURCE = "tushare"

# daily 族按交易日分区；fina_indicator 按 ann_date 月分区；stock_basic 单块全量。
# aux 族（B 阶段）：top_list/margin/cyq 按交易日；holder 按 ts_code（报告期）。
_DAILY_FAMILY = ("daily", "daily_basic", "moneyflow", "adj_factor")
_AUX_DAILY_FAMILY = ("top_list", "top_inst", "margin", "cyq")
_LHB_EMPTY_FAIL_CLOSED = frozenset({"top_list", "top_inst"})
_LHB_REVISION_DATASETS = frozenset({"top_list", "top_inst"})
DEFAULT_LHB_REVISION_RECHECK_DAYS = 5
PRODUCTION_DB_NAME = "stock_data.db"

# 数据集短名全集（表名 = {ds}_history）：checkpoint/CLI/coverage 统一用短名。
ALL_DATASETS = tuple(
    sorted(
        t[: -len("_history")]
        for t in HISTORY_TABLES
        if not t.startswith("lhb_official")
    )
)


def assert_copy_database(db_path: str | Path, *, maintenance_authorized: bool = False) -> Path:
    """真实回填只允许绝对路径副本；默认拒绝 runtime/stock_data.db。"""
    path = Path(db_path)
    if not path.is_absolute():
        raise ValueError("数据库路径必须是绝对路径（防误操作）")
    resolved = path.resolve()
    is_prod = resolved.name == PRODUCTION_DB_NAME and "runtime" in resolved.parts
    if is_prod and not maintenance_authorized:
        raise ValueError(f"拒绝操作生产库: {resolved}")
    return resolved


def _hash_rows(rows: list[dict[str, Any]]) -> str:
    row_hashes = sorted(
        hashlib.sha256(str(sorted(r.items())).encode("utf-8")).hexdigest() for r in rows
    )
    blob = "\n".join(row_hashes)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


class PitBackfill:
    """断点续跑回填器：进程中断后从最后一个未完成分区继续。"""

    def __init__(self, db_path: str | Path, pro: Any | None = None):
        self.db_path = str(db_path)
        self._pro = pro  # 离线测试注入 fake；None 时走根 tushare_init

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def trade_dates(self, start: str, end: str) -> list[str]:
        """从交易日历取开市日（生产走根 pro.trade_cal；测试注入 fake）。"""
        handle = get_pro_handle(self._pro)
        cal = handle.trade_cal(
            exchange="", start_date=start, end_date=end, fields="cal_date,is_open"
        )
        df = cal
        if hasattr(df, "loc"):
            df = df.loc[df["is_open"] == 1, "cal_date"].astype(str).tolist()
        else:
            df = [str(r["cal_date"]) for r in df if r.get("is_open") == 1]
        return sorted(df)

    def _fetch_partition(self, dataset: str, key: str) -> list[dict[str, Any]]:
        handle = get_pro_handle(self._pro)
        if dataset == "daily":
            df = handle.daily(trade_date=key)
        elif dataset == "daily_basic":
            df = handle.daily_basic(trade_date=key)
        elif dataset == "moneyflow":
            df = handle.moneyflow(trade_date=key)
        elif dataset == "adj_factor":
            df = handle.adj_factor(trade_date=key)
        elif dataset == "fina_indicator":
            df = handle.fina_indicator(ts_code=key)  # key = ts_code（全历史报告期）
        elif dataset == "stock_basic":
            df = handle.stock_basic()
        elif dataset == "top_list":
            df = handle.top_list(trade_date=key)
        elif dataset in ("top_inst", "hm_list"):
            return fetch_pit_rows(dataset, start=key, end=key, pro=self._pro)
        elif dataset == "margin":
            df = handle.margin_detail(trade_date=key)
        elif dataset == "cyq":
            df = handle.cyq_perf(trade_date=key)
        elif dataset == "holder":
            df = handle.top10_holders(ts_code=key)  # key = ts_code（报告期全量）
        else:
            raise ValueError(f"未知 PIT 数据集: {dataset}")
        return df_to_pit_rows(df, dataset)

    def checkpoint_partitions(self, datasets: Iterable[str]) -> dict[str, list[str]]:
        """从 pit_backfill_checkpoints 读取各数据集既有分区键（断点续跑离线计划）。"""
        out: dict[str, list[str]] = {}
        with self._conn() as conn:
            for ds in datasets:
                rows = conn.execute(
                    "SELECT partition_key FROM pit_backfill_checkpoints WHERE dataset=?",
                    (ds,),
                ).fetchall()
                if rows:
                    out[ds] = sorted({str(r[0]) for r in rows})
        return out

    def run(
        self,
        datasets: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        partitions: dict[str, list[str]] | None = None,
        workers: int = 1,
        lhb_revision_recheck_days: int = DEFAULT_LHB_REVISION_RECHECK_DAYS,
        progress_cb: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        """按数据集顺序回填。partitions 显式给出时优先（离线测试）。

        workers>1：拉取（网络）并发执行，写库保持串行（SQLite 单写者）；
        拉取失败重试 2 次后记录 failures 并继续（断点续跑兜底）。
        """
        ds_list = list(datasets)
        plan = self._plan(ds_list, start=start, end=end, partitions=partitions)
        available = normalize_ts(datetime.now(_TZ))
        total_done = 0
        total_rows = 0
        total_skipped = 0
        total_failed = 0
        total_revalidated = 0
        total_revised = 0
        per_dataset: dict[str, dict[str, Any]] = {}

        for dataset, keys in plan.items():
            done = 0
            skipped = 0
            rows = 0
            failed: list[str] = []
            revalidated = 0
            revised = 0
            pending: list[str] = []
            previous_hashes: dict[str, str] = {}
            recheck_keys = (
                set(keys[-max(0, lhb_revision_recheck_days):])
                if dataset in _LHB_REVISION_DATASETS and lhb_revision_recheck_days > 0
                else set()
            )
            with self._conn() as conn:
                for key in keys:
                    cp = conn.execute(
                        "SELECT status, source_hash FROM pit_backfill_checkpoints"
                        " WHERE dataset=? AND partition_key=?", (dataset, key)
                    ).fetchone()
                    if cp and cp[0] == "done":
                        if key in recheck_keys:
                            pending.append(key)
                            previous_hashes[key] = str(cp[1] or "")
                        else:
                            skipped += 1
                    else:
                        pending.append(key)
            # 并发预拉取（分批，控制内存）
            BATCH = 64
            for batch_start in range(0, len(pending), BATCH):
                batch = pending[batch_start:batch_start + BATCH]
                fetched: dict[str, Any] = {}
                if workers > 1 and len(batch) > 1:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(self._fetch_with_retry, dataset, k): k for k in batch
                        }
                        for fut in as_completed(futures):
                            k = futures[fut]
                            try:
                                fetched[k] = fut.result()
                            except Exception as exc:  # noqa: BLE001
                                fetched[k] = {"__error__": f"{type(exc).__name__}: {exc}"}
                else:
                    for k in batch:
                        try:
                            fetched[k] = self._fetch_with_retry(dataset, k)
                        except Exception as exc:  # noqa: BLE001
                            fetched[k] = {"__error__": f"{type(exc).__name__}: {exc}"}
                for key in batch:
                    chunk = fetched[key]
                    if isinstance(chunk, dict) and "__error__" in chunk:
                        failed.append(f"{key}: {chunk['__error__']}")
                        continue
                    if dataset in _LHB_EMPTY_FAIL_CLOSED and len(chunk) == 0:
                        failed.append(f"{key}: EMPTY_WITHOUT_PUBLISHED_FLAG")
                        continue
                    if len(chunk) > MAX_ROWS_PER_TX:
                        raise ValueError(
                            f"分区 {dataset}/{key} 行数 {len(chunk)} 超预算 {MAX_ROWS_PER_TX}"
                        )
                    src_hash = _hash_rows(chunk)
                    previous_hash = previous_hashes.get(key)
                    if previous_hash is not None and previous_hash == src_hash:
                        with self._conn() as conn:
                            conn.execute(
                                "UPDATE pit_backfill_checkpoints SET updated_at=?"
                                " WHERE dataset=? AND partition_key=?",
                                (_now_iso(), dataset, key),
                            )
                            conn.commit()
                        skipped += 1
                        revalidated += 1
                        continue
                    with self._conn() as conn:
                        conn.execute(
                            "INSERT INTO pit_backfill_checkpoints (dataset, partition_key, status,"
                            " last_key, row_count, source_hash, updated_at)"
                            " VALUES (?,?,'in_progress',?,?,?,?)"
                            " ON CONFLICT(dataset, partition_key) DO UPDATE SET status='in_progress',"
                            " last_key=excluded.last_key, row_count=excluded.row_count,"
                            " source_hash=excluded.source_hash, updated_at=excluded.updated_at",
                            (dataset, key, key, len(chunk), src_hash, _now_iso()),
                        )
                        conn.commit()
                        records = build_records(
                            dataset, chunk, source=SOURCE, available_at=available, conn=conn
                        )
                        write_chunk(
                            conn, dataset, records,
                            partition_key=key, source=SOURCE, available_at=available,
                        )
                        conn.execute(
                            "UPDATE pit_backfill_checkpoints SET status='done', row_count=?,"
                            " updated_at=? WHERE dataset=? AND partition_key=?",
                            (len(records), _now_iso(), dataset, key),
                        )
                        conn.commit()
                    done += 1
                    if previous_hash is not None:
                        revised += 1
                    rows += len(chunk)
                    if progress_cb:
                        progress_cb(f"{dataset}/{key}", rows)
            per_dataset[dataset] = {
                "partitions_done": done, "rows": rows, "skipped": skipped,
                "failed": failed, "revalidated": revalidated, "revised": revised,
            }
            total_done += done
            total_rows += rows
            total_skipped += skipped
            total_failed += len(failed)
            total_revalidated += revalidated
            total_revised += revised

        return {
            "datasets": sorted(per_dataset),
            "partitions_done": total_done,
            "rows": total_rows,
            "skipped": total_skipped,
            "failed": total_failed,
            "revalidated": total_revalidated,
            "revised": total_revised,
            "per_dataset": per_dataset,
        }

    def _fetch_with_retry(self, dataset: str, key: str) -> list[dict[str, Any]]:
        """拉取分区（重试 2 次）；仍失败抛错由调用方记录。"""
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                return self._fetch_partition(dataset, key)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < 2:
                    from time import sleep

                    sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"{dataset}/{key} 拉取失败: {last_err}") from last_err

    def _plan(
        self,
        datasets: list[str],
        *,
        start: str | None,
        end: str | None,
        partitions: dict[str, list[str]] | None,
    ) -> dict[str, list[str]]:
        plan: dict[str, list[str]] = {}
        for ds in datasets:
            table = f"{ds}_history"
            if table not in HISTORY_TABLES:
                raise ValueError(f"未知 PIT 数据集: {ds}")
            if partitions and ds in partitions:
                plan[ds] = sorted(partitions[ds])
            elif ds in _DAILY_FAMILY or ds in _AUX_DAILY_FAMILY:
                if not start or not end:
                    raise ValueError(f"{ds} 需要 start/end 以推导交易日分区")
                plan[ds] = self.trade_dates(start, end)
            elif ds == "fina_indicator":
                codes = self._basic_ts_codes()
                if not codes:
                    raise ValueError(
                        "fina_indicator 回填需要 stock_basic/delisted_basic 表提供 ts_code 分区键"
                        "（镜像网关不支持纯 period 查询，必须按标的拉取）"
                    )
                plan[ds] = sorted(codes)
            elif ds == "hm_list":
                if not end:
                    raise ValueError("hm_list 需要 end（list_date）")
                plan[ds] = [end]
            elif ds == "holder":
                codes = self._basic_ts_codes()
                if not codes:
                    raise ValueError(
                        "holder 回填需要 stock_basic/delisted_basic 表提供 ts_code 分区键"
                        "（先同步基础信息）"
                    )
                plan[ds] = sorted(codes)
            else:
                plan[ds] = ["ALL"]
        return plan

    def _basic_ts_codes(self) -> list[str]:
        """标的分区键 = 上市 + 退市 ts_code（从非 PIT 同步表读取）。"""
        codes: set[str] = set()
        try:
            with self._conn() as conn:
                for table in ("stock_basic", "delisted_basic"):
                    try:
                        rows = conn.execute(f"SELECT ts_code FROM {table}").fetchall()
                        codes.update(r[0] for r in rows)
                    except sqlite3.OperationalError:
                        continue
        except sqlite3.OperationalError:
            return []
        return sorted(codes)

    def coverage_report(self, datasets: Iterable[str] | None = None) -> dict[str, Any]:
        """各数据集已回填分区/行数；all_done 判定供门禁使用。"""
        ds_list = list(datasets) if datasets is not None else list(ALL_DATASETS)
        report: dict[str, Any] = {}
        with self._conn() as conn:
            for ds in ds_list:
                total = conn.execute(
                    "SELECT COUNT(*) FROM pit_backfill_checkpoints WHERE dataset=?", (ds,)
                ).fetchone()[0]
                done = conn.execute(
                    "SELECT COUNT(*) FROM pit_backfill_checkpoints"
                    " WHERE dataset=? AND status='done'", (ds,)
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT COALESCE(SUM(row_count),0) FROM pit_backfill_checkpoints"
                    " WHERE dataset=? AND status='done'", (ds,)
                ).fetchone()[0]
                report[ds] = {"partitions": total, "done": done, "rows": int(rows)}
        report["all_done"] = bool(ds_list) and all(
            v["partitions"] > 0 and v["done"] == v["partitions"] for v in report.values()
        )
        return report


class CorporateActionBackfill:
    """公司行为同步：按 ts_code 分区，checkpoint 断点续跑。

    V2R-D 契约：
    - 每条记录携带 effective_at / available_at / ingested_at / source / revision。
    - checkpoint 记录最后完成分区；一个分区内任一条目失败都不得标记 done
      （不允许部分分区被标记完成）。
    - 无权限/接口异常 → 分区失败记录，不伪装成功（fail-closed）。
    - 重复抓取幂等：同一载荷重复拉取由 CorporateActionRepository.append 幂等跳过。
    """

    CHECKPOINT_DATASET = "corporate_action"

    def __init__(self, db_path: str | Path, pro: Any | None = None):
        self.db_path = str(db_path)
        self._pro = pro

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def run(
        self,
        ts_codes: Iterable[str],
        *,
        available_at: Any | None = None,
        progress_cb: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        """按 ts_code 分区同步公司行为；done 分区跳过（断点续跑）。

        available_at 缺省用当前时刻；每个分区在全部条目入库成功后统一标记 done。
        """
        codes = sorted({str(c) for c in ts_codes if c})
        repo = CorporateActionRepository(self.db_path)
        available = normalize_ts(available_at or datetime.now(_TZ))
        done: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, Any]] = []
        with self._conn() as conn:
            for code in codes:
                cp = conn.execute(
                    "SELECT status FROM pit_backfill_checkpoints"
                    " WHERE dataset=? AND partition_key=?",
                    (self.CHECKPOINT_DATASET, code),
                ).fetchone()
                if cp and cp[0] == "done":
                    skipped.append(code)
                    continue
                try:
                    rows = fetch_corporate_actions(self._pro, ts_code=code)
                except CorporateActionError as exc:
                    failed.append({"code": code, "error": str(exc)})
                    continue
                except Exception as exc:  # noqa: BLE001
                    failed.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                appended = 0
                try:
                    for row in rows:
                        repo.append(
                            {
                                "ts_code": row["ts_code"],
                                "ex_date": row["ex_date"],
                                "kind": row["kind"],
                                "payload": row["payload"],
                                "source": row.get("source") or SOURCE,
                                "available_at": available,
                                "effective_at": row["ex_date"],
                            }
                        )
                        appended += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append({"code": code, "error": f"入账失败: {exc}"})
                    continue
                # 全部成功才标记 done（不允许部分分区被标记完成）
                conn.execute(
                    "INSERT INTO pit_backfill_checkpoints (dataset, partition_key, status,"
                    " last_key, row_count, source_hash, updated_at)"
                    " VALUES (?,?,'done',?,?,?,?)"
                    " ON CONFLICT(dataset, partition_key) DO UPDATE SET status='done',"
                    " last_key=excluded.last_key, row_count=excluded.row_count,"
                    " source_hash=excluded.source_hash, updated_at=excluded.updated_at",
                    (self.CHECKPOINT_DATASET, code, code, appended, _hash_rows(rows), _now_iso()),
                )
                conn.commit()
                done.append(code)
                if progress_cb:
                    progress_cb(f"{self.CHECKPOINT_DATASET}/{code}", appended)
        return {
            "dataset": self.CHECKPOINT_DATASET,
            "partitions_done": len(done),
            "partitions_skipped": len(skipped),
            "failed": failed,
            "failed_count": len(failed),
        }

    def coverage_report(self) -> dict[str, Any]:
        """公司行为同步覆盖率；all_done 判定供门禁使用。"""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM pit_backfill_checkpoints WHERE dataset=?",
                (self.CHECKPOINT_DATASET,),
            ).fetchone()[0]
            done = conn.execute(
                "SELECT COUNT(*) FROM pit_backfill_checkpoints"
                " WHERE dataset=? AND status='done'",
                (self.CHECKPOINT_DATASET,),
            ).fetchone()[0]
        return {
            "dataset": self.CHECKPOINT_DATASET,
            "partitions": int(total),
            "done": int(done),
            "all_done": int(total) > 0 and int(total) == int(done),
        }
