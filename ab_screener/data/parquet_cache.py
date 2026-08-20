"""Parquet 派生缓存：可删可重建；失效回退 SQLite，永不读 pickle。

缓存 key 必须包含区间内**每一个**交易日分区的 content_sha256；
任一中间日期数据变更都会 miss，禁止只校验首尾日期。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ab_screener.data.repository import MarketRepository

_CACHE_ROOT = Path(__file__).resolve().parents[2] / "runtime" / "parquet_cache"


def _key(
    *,
    start: str,
    end: str,
    columns: list[str],
    codes: list[str] | None,
    partition_hashes: list[str],
) -> str:
    payload = {
        "start": start,
        "end": end,
        "columns": columns,
        "codes_sha": hashlib.sha256(
            ("\n".join(sorted(codes)) if codes else "").encode()
        ).hexdigest()[:16],
        "n_codes": len(codes) if codes else None,
        # 全量分区指纹列表（顺序=日期升序）
        "parts": partition_hashes,
        "parts_sha": hashlib.sha256("\n".join(partition_hashes).encode()).hexdigest()[:16],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def _ensure_partition_hashes(
    repo: MarketRepository,
    dates: list[str],
    *,
    dataset: str = "daily",
) -> list[str]:
    """对区间内每个交易日返回 content_sha256；缺失则计算并写入 dataset_partitions。"""
    out: list[str] = []
    for d in dates:
        fp = repo.partition_fingerprint(dataset, d)
        if not fp:
            n, fp = repo.compute_daily_day_hash(d)
            try:
                repo.upsert_partition(dataset, d, n, fp)
            except Exception:  # noqa: BLE001
                pass
        out.append(fp or "")
    return out


def load_daily_cached(
    repo: MarketRepository,
    *,
    start: str,
    end: str,
    codes: list[str] | None = None,
    columns: list[str] | None = None,
    cache_dir: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """返回 (df, meta)。meta 含 cache_hit / path / key。"""
    root = Path(cache_dir or _CACHE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    cols = columns or [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "vol", "amount", "pct_chg", "pre_close",
    ]
    dates = [d for d in repo.distinct_dates("daily") if start <= d <= end]
    part_hashes = _ensure_partition_hashes(repo, dates)
    key = _key(start=start, end=end, columns=cols, codes=codes, partition_hashes=part_hashes)
    path = root / f"daily_{key}.parquet"
    meta: dict[str, Any] = {
        "cache_hit": False,
        "path": str(path),
        "key": key,
        "source": "sqlite",
        "n_partitions": len(part_hashes),
    }

    if path.exists():
        try:
            df = pd.read_parquet(path)
            meta["cache_hit"] = True
            meta["source"] = "parquet"
            return df, meta
        except Exception:  # noqa: BLE001
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    df = repo.load_daily(start=start, end=end, codes=codes, columns=cols)
    try:
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
        meta["source"] = "sqlite+written"
    except Exception:  # noqa: BLE001
        meta["source"] = "sqlite"
        meta["parquet_error"] = "write_failed"
    return df, meta
