"""抓取 Tushare 历史名称（namechange）→ runtime/research/reference/namechange.csv（ADR-022 修订 1）。

- 只写研究参考文件，不建表、不迁移数据库；附 .meta.json（抓取时间、行数、SHA-256）。
- offset/limit 翻页；网关忽略 offset 或连续满页超过上限 → 拒绝写入（不静默截断）。
- 已有文件时写新版本并保留旧文件（namechange-<时间戳>.csv），当前版本为 namechange.csv。

用法：.venv312\\Scripts\\python.exe scripts\\sync_namechange.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TZ = ZoneInfo("Asia/Shanghai")
FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"
PAGE = 5000
MAX_PAGES = 40


def fetch_all(provider, *, page: int = PAGE, max_pages: int = MAX_PAGES) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    seen: set[tuple[str, str, str]] = set()
    for k in range(max_pages):
        frame = provider.namechange(fields=FIELDS, offset=k * page, limit=page)
        if frame is None or frame.empty:
            break
        keys = set(zip(frame["ts_code"].astype(str), frame["name"].astype(str),
                       frame["start_date"].astype(str), strict=True))
        if keys and keys <= seen:
            raise RuntimeError("数据网关忽略 namechange 的 offset；拒绝写入可能被截断的名称历史")
        seen |= keys
        frames.append(frame)
        if len(frame) < page:
            break
    else:
        raise RuntimeError(f"namechange 连续 {max_pages} 页满页，可能未取完；拒绝写入")
    if not frames:
        raise RuntimeError("namechange 返回为空")
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    missing = {"ts_code", "name", "start_date"} - set(out.columns)
    if missing:
        raise RuntimeError(f"namechange 缺少字段 {sorted(missing)}")
    return out.sort_values(["ts_code", "start_date", "name"]).reset_index(drop=True)


def write_reference(frame: pd.DataFrame, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_TZ)
    target = out_dir / "namechange.csv"
    if target.exists():
        target.rename(out_dir / f"namechange-{stamp.strftime('%Y%m%dT%H%M%S')}.prev.csv")
    frame.to_csv(target, index=False, encoding="utf-8")
    meta = {
        "dataset": "namechange",
        "fetched_at": stamp.isoformat(timespec="seconds"),
        "rows": len(frame),
        "codes": int(frame["ts_code"].nunique()),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "availability": "ADR-022 修订 1：start_date 当日 00:00",
    }
    (out_dir / "namechange.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取历史名称到研究参考目录")
    parser.add_argument("--out", default="runtime/research/reference")
    args = parser.parse_args(argv)
    from tushare_init import get_pro, sanitize_error

    try:
        frame = fetch_all(get_pro())
    except Exception as exc:  # noqa: BLE001 - 供应商异常统一脱敏
        print(f"REFUSED: {sanitize_error(exc)}")
        return 2
    meta = write_reference(frame, Path(args.out))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
