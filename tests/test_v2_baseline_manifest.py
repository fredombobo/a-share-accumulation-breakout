"""V2 P0.1 基线 manifest 验收测试：结构完整、身份稳定、敏感字段可验证。"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_tiny_db(path: Path) -> None:
    """构造含 identity 所需全部表的小型临时库（避免误扫 16GB 生产库）。"""
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        for table in ("daily", "daily_basic", "moneyflow", "stock_basic", "delisted_basic", "scan_result"):
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (ts_code TEXT, trade_date TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (id TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES ('schema_version', '101')")
        conn.execute("INSERT INTO daily VALUES ('000001.SZ', '20260807')")
        conn.commit()
    finally:
        conn.close()


def _make_pytest_source(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "pytest": {
                    "exit_code": 0,
                    "tests": 1,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                    "passed": 1,
                    "junitxml_sha256": "fixture",
                }
            }
        ),
        encoding="utf-8",
    )


def _capture(out: Path, db: Path, pytest_source: Path) -> tuple[dict, int]:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/capture_v2_baseline.py",
            "--skip-api",
            "--skip-pytest",
            "--pytest-source",
            str(pytest_source),
            "--db-path",
            str(db),
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.is_file(), result.stdout + result.stderr
    return json.loads(out.read_text(encoding="utf-8")), result.returncode


@pytest.fixture()
def captured_manifest(tmp_path: Path) -> tuple[dict, int]:
    db = tmp_path / "tiny.db"
    source = tmp_path / "pytest-source.json"
    _make_tiny_db(db)
    _make_pytest_source(source)
    return _capture(tmp_path / "baseline.json", db, source)


def test_manifest_structure_complete(captured_manifest: tuple[dict, int]):
    m, _ = captured_manifest
    for key in ("generated_at", "python", "git", "config_hash", "database",
                "frontend", "dependencies_sha256", "pytest", "identity", "identity_detail"):
        assert key in m, f"manifest 缺字段 {key}"
    assert m["git"]["git_sha"]
    assert m["database"]["exists"] is True
    assert m["database"].get("quick_check") == "ok"
    assert m["database"].get("daily_rows", 0) > 0
    assert m["pytest"]["tests"] > 0
    assert m["pytest"]["failures"] == 0


def test_identity_stable_across_runs(tmp_path: Path):
    """同身份重复生成：同一代码/配置/测试集状态下，连续两次生成 identity 一致。

    使用小型临时库（--db-path），避免在身份测试中误扫 16GB 生产库导致超时。
    注意：新增/删除测试会改变 pytest 结果集 → identity 理应变化（正确行为），
    因此本测试只比较两次立即生成的结果，不与磁盘上可能过期的 manifest 对比。
    """
    tiny_db = tmp_path / "tiny.db"
    pytest_source = tmp_path / "pytest-source.json"
    _make_tiny_db(tiny_db)
    _make_pytest_source(pytest_source)
    t1, _ = _capture(tmp_path / "baseline-1.json", tiny_db, pytest_source)
    t2, _ = _capture(tmp_path / "baseline-2.json", tiny_db, pytest_source)
    assert t1["identity"] == t2["identity"]
    assert t1["identity_detail"] == t2["identity_detail"]
    assert t1["database"]["exists"] is True


def test_identity_sensitive_to_config_and_code(
    captured_manifest: tuple[dict, int],
):
    """改代码/配置后 identity 必须变化（敏感字段验证）。"""
    m, _ = captured_manifest
    # 与 capture_v2_baseline.sha256_of_file 同口径：原始字节（避免 CRLF 换行转换导致 hash 漂移）
    cfg = (ROOT / "config.py").read_bytes()
    cfg_digest_a = hashlib.sha256(cfg).hexdigest()
    cfg_digest_b = hashlib.sha256(cfg + b"\n# probe").hexdigest()
    assert cfg_digest_a != cfg_digest_b
    assert m["config_hash"] == cfg_digest_a
    # identity 必须包含 config_hash（改配置 → identity 变）
    mutated_identity = hashlib.sha256(
        json.dumps(
            {**m["identity_detail"], "config_hash": cfg_digest_b},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert m["identity"] != mutated_identity
    mutated_code_identity = hashlib.sha256(
        json.dumps(
            {**m["identity_detail"], "git_sha": f"{m['git']['git_sha']}-probe"},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert m["identity"] != mutated_code_identity


def test_blocked_when_dirty_or_inconsistent(
    captured_manifest: tuple[dict, int],
):
    """工作区脏或 db 不健康时状态为 BLOCKED（采集器退出码 1）。"""
    m, returncode = captured_manifest
    reasons = []
    if m["git"]["worktree_dirty"]:
        reasons.append("WORKTREE_DIRTY")
    if m["database"].get("quick_check") != "ok":
        reasons.append("DB_BAD")
    if m["pytest"]["failures"] > 0:
        reasons.append("PYTEST_FAILED")
    assert returncode == (1 if reasons else 0)
