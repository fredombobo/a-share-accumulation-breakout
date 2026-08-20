"""研究产物清单（P3.4）：生成/校验全量产物哈希清单。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactManifestError(ValueError):
    """清单非法（fail-closed）。"""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(artifacts: dict[str, str | Path]) -> dict[str, Any]:
    """artifacts: {名称: 路径} → {name: {path, sha256}}。"""
    manifest: dict[str, Any] = {}
    for name, path in sorted(artifacts.items()):
        p = Path(path)
        if not p.is_file():
            raise ArtifactManifestError(f"产物不存在: {p}")
        manifest[name] = {"path": str(p), "sha256": sha256_file(p)}
    return manifest


def manifest_sha256(manifest: dict[str, Any]) -> str:
    blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def verify_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """逐项核对：当前文件哈希 == 清单哈希；任何不匹配 → 整体无效。"""
    mismatches: list[str] = []
    for name, entry in manifest.items():
        p = Path(entry["path"])
        if not p.is_file():
            mismatches.append(f"{name}: 文件缺失")
            continue
        if sha256_file(p) != entry["sha256"]:
            mismatches.append(f"{name}: 内容哈希不一致（可能被篡改）")
    return {
        "valid": not mismatches,
        "mismatches": mismatches,
        "items": len(manifest),
    }
