"""P6.3 备份/恢复/健康测试：online backup、原子命名、保留策略、健康聚合。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.operations.backup import (
    BackupError,
    backup_ok,
    create_backup,
    prune_old_backups,
)
from ab_screener.operations.health import system_health


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "src.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("x",), ("y",), ("z",)])
    conn.commit()
    conn.close()
    return str(path)


def test_backup_created_verified(db: str, tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir()
    result = create_backup(db, root)
    assert Path(result["path"]).is_file()
    assert result["tables"] >= 1
    # 校验：备份可读且行数一致
    conn = sqlite3.connect(result["path"])
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    conn.close()


def test_backup_atomic_and_prune_keeps_unique(db: str, tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir()
    for _ in range(3):
        create_backup(db, root)
    # 保留策略：超过 keep 才删
    assert len(list(root.glob("backup_*.db"))) == 3
    # 唯一备份绝不删除
    only_root = tmp_path / "only"
    only_root.mkdir()
    create_backup(db, only_root)
    assert prune_old_backups(only_root, keep=7) == []
    assert len(list(only_root.glob("backup_*.db"))) == 1


def test_backup_ok_thresholds(db: str, tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir()
    check = backup_ok(root)
    assert check["ok"] is False  # 无备份
    for _ in range(7):
        create_backup(db, root)
    check = backup_ok(root)
    assert check["count"] >= 7 and check["latest_age_hours"] < 24
    assert check["ok"] is True


def test_backup_missing_source_fail_closed(tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir()
    with pytest.raises(BackupError, match="不存在"):
        create_backup(tmp_path / "nope.db", root)


def test_system_health(db: str, tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir()
    # 无备份 → FAIL
    health = system_health(db, root)
    assert health["status"] == "FAIL"
    assert any("备份" in i for i in health["issues"])
    # 有备份 → PASS（数据库健康）
    for _ in range(7):
        create_backup(db, root)
    health2 = system_health(db, root)
    assert health2["status"] == "PASS"
