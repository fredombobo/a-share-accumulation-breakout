"""bootstrap 参数与 Token 写入自检（不联网、不启服务）"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bootstrap


def test_normalize_and_resolve(tmp_path: Path | None = None):
    assert bootstrap._normalize_token('  abc  ') == "abc"
    assert bootstrap._normalize_token("TUSHARE_TOKEN=xyz") == "xyz"
    assert bootstrap._token_ok("your_token_here") is False
    assert bootstrap._token_ok("short") is False
    assert bootstrap._token_ok("real_token_value_ok") is True
    print("[PASS] token normalize/ok")


def test_write_env(monkeypatch_dir: Path | None = None):
    root = Path(tempfile.mkdtemp())
    env = root / ".env"
    # 临时替换路径
    old_env, old_ex = bootstrap.ENV_PATH, bootstrap.ENV_EXAMPLE
    bootstrap.ENV_PATH = env
    bootstrap.ENV_EXAMPLE = root / ".env.example"
    bootstrap.ENV_EXAMPLE.write_text("TUSHARE_TOKEN=your_token_here\nTUSHARE_HTTP_URL=http://x/\n", encoding="utf-8")
    try:
        bootstrap.write_env("demo_token_12345678")
        text = env.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=demo_token_12345678" in text
        assert "your_token_here" not in text
        # resolve
        os.environ.pop("TUSHARE_TOKEN", None)
        t = bootstrap.resolve_token(None)
        assert t == "demo_token_12345678"
        print("[PASS] write_env + resolve")
    finally:
        bootstrap.ENV_PATH = old_env
        bootstrap.ENV_EXAMPLE = old_ex


def test_cli_help():
    import subprocess
    r = subprocess.run(
        [sys.executable, "bootstrap.py", "--help"], cwd=os.path.dirname(__file__),
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0
    assert "--token" in r.stdout
    print("[PASS] cli --help")


if __name__ == "__main__":
    test_normalize_and_resolve()
    test_write_env()
    test_cli_help()
    print("\nbootstrap 自检通过 ✅")
