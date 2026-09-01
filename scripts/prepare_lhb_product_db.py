"""创建/迁移龙虎榜产品数据库副本；源库只读，绝不原地迁移生产库。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.application.pit_backfill import assert_copy_database
from ab_screener.data.migration_intents import register_lhb_intents
from ab_screener.data.migration_registry import apply_pending, schema_compatible

# 本脚本专门准备龙虎榜隔离副本，必须显式打开 LHB 迁移意图。
register_lhb_intents()


def prepare(source: Path, target: Path) -> dict[str, object]:
    if not source.is_absolute() or not target.is_absolute():
        raise ValueError("source/target 必须是绝对路径")
    source = source.resolve()
    target = assert_copy_database(target, maintenance_authorized=False)
    if source == target:
        raise ValueError("源库与目标副本不能相同")
    if not source.is_file():
        raise ValueError(f"源库不存在: {source}")
    created = False
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        src: sqlite3.Connection | None = None
        dst: sqlite3.Connection | None = None
        try:
            src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
            dst = sqlite3.connect(str(target), timeout=60)
            src.backup(dst, pages=8192)
            dst.commit()
        except Exception:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()
            if target.is_file() and target.stat().st_size == 0:
                target.unlink()
            raise
        finally:
            if dst is not None:
                try:
                    dst.close()
                except Exception:  # noqa: BLE001
                    pass
            if src is not None:
                try:
                    src.close()
                except Exception:  # noqa: BLE001
                    pass
        created = True
    with sqlite3.connect(str(target), timeout=60) as conn:
        applied = apply_pending(conn)
    ok, issues = schema_compatible(target)
    if not ok:
        raise RuntimeError(f"副本迁移后仍不兼容: {issues}")
    return {
        "source": str(source),
        "target": str(target),
        "created": created,
        "applied": applied,
        "schema_compatible": ok,
        "size_bytes": target.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="准备龙虎榜产品数据库副本")
    parser.add_argument("--source", required=True, help="只读源库绝对路径")
    parser.add_argument("--target", required=True, help="产品副本绝对路径")
    args = parser.parse_args()
    result = prepare(Path(args.source), Path(args.target))
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"启动前设置: AB_DB_PATH={result['target']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
