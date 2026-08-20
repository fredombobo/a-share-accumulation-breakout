"""V2 P0.1 基线采集器：生成可复算的 runtime/v2/baseline_manifest.json。

用途：机构化 v2 的 P0 基线重测。收集代码/配置/数据/测试/前端/API 的结构化事实，
产出 identity 哈希供验收测试断言「同身份稳定、改代码后变化」。

用法（权威证据环境）：
  .venv312\\Scripts\\python.exe scripts\\capture_v2_baseline.py [--out runtime/v2/baseline_manifest.json] [--skip-api]

前置：后端已在 127.0.0.1:8001 运行（否则 api_snapshot 标记 skipped 并令状态 BLOCKED）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_facts() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                timeout=20, check=False,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    sha = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain") != ""
    return {"git_sha": sha, "worktree_dirty": bool(dirty)}


def config_hash() -> str:
    return sha256_of_file(ROOT / "config.py")


def db_facts(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"exists": False}
    facts: dict = {"exists": True, "size_bytes": db_path.stat().st_size}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        for table in ("daily", "daily_basic", "moneyflow", "stock_basic", "delisted_basic", "scan_result"):
            try:
                row = conn.execute(
                    f"SELECT COUNT(*), COALESCE(MAX(trade_date),'') FROM {table}"
                ).fetchone()
                facts[f"{table}_rows"] = int(row[0])
                facts[f"{table}_max_trade_date"] = str(row[1])
            except Exception:  # noqa: BLE001
                pass
        try:
            facts["schema_version"] = conn.execute(
                "SELECT value FROM schema_version WHERE id='schema_version'"
            ).fetchone()[0] if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone() else None
        except Exception:  # noqa: BLE001
            facts["schema_version"] = None
        try:
            facts["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        except Exception:  # noqa: BLE001
            facts["quick_check"] = None
        conn.close()
    except Exception as exc:  # noqa: BLE001
        facts["error"] = str(exc)[:200]
    return facts


def pytest_results() -> dict:
    """运行离线 pytest（-k not browser），用 junitxml 汇总。"""
    xml = ROOT / "runtime" / "v2" / "pytest_baseline.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pytest", "tests", "-q", "-k", "not browser",
        "--ignore", "tests/test_v2_baseline_manifest.py",
        "--junitxml", str(xml), "-p", "no:cacheprovider",
    ]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1800, check=False)
    duration = round(time.time() - t0, 2)
    tests = failures = errors = skipped = 0
    if xml.is_file():
        import xml.etree.ElementTree as ET

        root = ET.parse(str(xml)).getroot()
        # pytest junitxml：根为 <testsuites>，数字在子 <testsuite> 上（可能多个，求和）
        suites = [root] if root.tag == "testsuite" else list(root)
        for suite in suites:
            tests += int(suite.attrib.get("tests", 0))
            failures += int(suite.attrib.get("failures", 0))
            errors += int(suite.attrib.get("errors", 0))
            skipped += int(suite.attrib.get("skipped", 0))
    return {
        "exit_code": r.returncode,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": tests - failures - errors - skipped,
        "duration_sec": duration,
        "junitxml_sha256": sha256_of_file(xml),
        "tail": r.stdout.strip().splitlines()[-4:] if r.stdout else [],
    }


def frontend_facts() -> dict:
    dist = ROOT / "web" / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        return {"exists": False}
    assets = sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for rel in assets:
        digest.update(rel.encode("utf-8"))
        digest.update(sha256_of_file(dist / rel).encode("utf-8"))
    return {
        "exists": True,
        "index_sha256": sha256_of_file(dist / "index.html"),
        "asset_count": len(assets),
        "bundle_hash": digest.hexdigest(),
    }


def api_snapshot(base: str = "http://127.0.0.1:8001") -> dict:
    import urllib.request

    out: dict = {}
    for path in ("/api/health", "/api/release/readiness", "/api/lab/research-status?probe_token=false"):
        try:
            with urllib.request.urlopen(base + path, timeout=8) as r:
                out[path] = {"status": r.status, "body": json.loads(r.read().decode("utf-8"))}
        except Exception as exc:  # noqa: BLE001
            out[path] = {"status": "ERROR", "error": str(exc)[:200]}
    return out


def dependency_hash() -> str:
    digest = hashlib.sha256()
    for req in ("requirements.txt", "requirements-dev.txt"):
        digest.update(sha256_of_file(ROOT / req).encode("utf-8"))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runtime/v2/baseline_manifest.json")
    parser.add_argument("--skip-api", action="store_true", help="跳过后端 API 快照")
    parser.add_argument("--skip-pytest", action="store_true",
                        help="不重跑全量 pytest，从 --pytest-source 复用测试结果（身份稳定性验证用）")
    parser.add_argument("--pytest-source", default="runtime/v2/baseline_manifest.json",
                        help="--skip-pytest 时读取 pytest 结果的 manifest")
    args = parser.parse_args()

    git = git_facts()
    cfg = config_hash()
    db = db_facts(ROOT / "runtime" / "stock_data.db")
    front = frontend_facts()
    deps = dependency_hash()

    manifest: dict = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        },
        "git": git,
        "config_hash": cfg,
        "database": db,
        "frontend": front,
        "dependencies_sha256": deps,
    }

    if args.skip_pytest:
        src = ROOT / args.pytest_source
        if src.is_file():
            manifest["pytest"] = json.loads(src.read_text(encoding="utf-8")).get("pytest", {})
            manifest["pytest"]["reused_from"] = str(src)
        else:
            manifest["pytest"] = {"reused_from": None, "note": "pytest skipped"}
    else:
        manifest["pytest"] = pytest_results()

    manifest["api_snapshot"] = {} if args.skip_api else api_snapshot()

    # identity：稳定字段的哈希（不含 generated_at、pytest.duration、api_snapshot、pytest.reused_from）
    pytest_stable = {k: v for k, v in manifest["pytest"].items()
                     if k not in ("duration_sec", "reused_from", "note", "tail")}
    stable = {
        "python_version": manifest["python"]["version"],
        "git_sha": git["git_sha"],
        "worktree_dirty": git["worktree_dirty"],
        "config_hash": cfg,
        "database": {k: v for k, v in db.items() if k != "size_bytes"},
        "frontend_bundle_hash": front.get("bundle_hash", ""),
        "dependencies_sha256": deps,
        "pytest": pytest_stable,
    }
    manifest["identity"] = sha256_of_bytes(json.dumps(stable, sort_keys=True).encode("utf-8"))[:16]
    manifest["identity_detail"] = stable

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pt = manifest["pytest"]
    print(f"baseline written: {out}")
    print(f"identity={manifest['identity']} pytest={pt.get('passed')}/{pt.get('tests')} "
          f"dirty={git['worktree_dirty']} db_ok={db.get('quick_check')}")

    # 状态判定：dirty 或 db 不健康或 API 失败 → BLOCKED
    blocked_reasons = []
    if git["worktree_dirty"]:
        blocked_reasons.append("WORKTREE_DIRTY")
    if db.get("quick_check") != "ok":
        blocked_reasons.append("DB_QUICK_CHECK_FAILED")
    if pt.get("exit_code") not in (0, None) or pt.get("failures", 0) > 0:
        blocked_reasons.append("PYTEST_FAILED")
    if not args.skip_api:
        for path, snap in manifest["api_snapshot"].items():
            if snap.get("status") != 200:
                blocked_reasons.append(f"API_{path}")
    print("status:", "BLOCKED" if blocked_reasons else "P0_BASELINE_OK", blocked_reasons)
    return 1 if blocked_reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
