"""build version 指纹验收：非空、稳定、结构正确、与 legacy_state 一致。"""
from __future__ import annotations

import os
import re
from pathlib import Path


def test_build_version_is_12_hex_chars() -> None:
    from build_version import build_version

    v = build_version()
    assert isinstance(v, str)
    assert len(v) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", v), v


def test_build_version_stable_across_calls() -> None:
    from build_version import build_version

    assert build_version() == build_version()


def test_fingerprints_are_sha256_prefixes() -> None:
    from build_version import backend_fingerprint, frontend_fingerprint

    assert isinstance(backend_fingerprint(), str)
    assert len(backend_fingerprint()) == 16
    assert isinstance(frontend_fingerprint(), str)
    assert len(frontend_fingerprint()) == 16


def test_legacy_state_build_version_matches_local() -> None:
    from ab_screener.api.legacy_state import _BUILD_VERSION
    from build_version import build_version

    assert _BUILD_VERSION == build_version()


def test_file_fingerprint_ignores_mtime_but_detects_content(tmp_path: Path) -> None:
    from build_version import _file_fp

    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    initial = _file_fp(source)
    os.utime(source, (source.stat().st_atime + 100, source.stat().st_mtime + 100))
    assert _file_fp(source) == initial
    source.write_text("value = 2\n", encoding="utf-8")
    assert _file_fp(source) != initial
