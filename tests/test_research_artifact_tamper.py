"""P3.4 产物清单测试：构建/整体哈希/篡改检测。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ab_screener.research.artifact_manifest import (
    ArtifactManifestError,
    build_manifest,
    manifest_sha256,
    verify_manifest,
)


def test_build_and_verify_manifest(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    b.write_text("report", encoding="utf-8")
    manifest = build_manifest({"alpha": a, "report": b})
    assert len(manifest) == 2
    assert manifest_sha256(manifest) == manifest_sha256(manifest)  # 确定性
    check = verify_manifest(manifest)
    assert check["valid"] is True and check["mismatches"] == []


def test_tamper_detected(tmp_path: Path):
    a = tmp_path / "a.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    manifest = build_manifest({"alpha": a})
    a.write_text('{"x": 999}', encoding="utf-8")  # 篡改
    check = verify_manifest(manifest)
    assert check["valid"] is False
    assert any("不一致" in m for m in check["mismatches"])


def test_missing_artifact_fail_closed(tmp_path: Path):
    with pytest.raises(ArtifactManifestError, match="不存在"):
        build_manifest({"nope": tmp_path / "missing.json"})
    # 清单中路径消失 → 校验失败
    a = tmp_path / "a.json"
    a.write_text("x", encoding="utf-8")
    manifest = build_manifest({"a": a})
    a.unlink()
    check = verify_manifest(manifest)
    assert check["valid"] is False
