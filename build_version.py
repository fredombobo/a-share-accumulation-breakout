"""构建指纹与版本检测（无第三方依赖）

供 backend_app.py / easy_start.py / bootstrap.py 共用：
- 后端启动时计算 build_version（源码指纹 + 前端 dist 指纹）
- 启动器（一键启动）在检测到后端已在运行时，比对 /api/health 返回的
  build_version 与本地指纹；不一致 = 源码或前端产物已更新 → 自动重启后端。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DIST = WEB / "frontend" / "dist"

_EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "dist", "runtime", ".venv", "venv"}
_PY_SUFFIXES = (".py", ".pyw")
_DIST_SUFFIXES = (".js", ".css", ".html", ".json", ".png", ".svg", ".ico")


def _file_fp(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return f"{p.name}:missing"


def _walk_files(root: Path, suffixes: tuple[str, ...], max_depth: int = 2) -> list[Path]:
    """有限深度收集文件，跳过构建/缓存目录。"""
    out: list[Path] = []
    if not root.is_dir():
        return out
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = sorted(d.iterdir(), key=lambda x: x.name)
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in _EXCLUDE_DIRS and e.name != ROOT.name:
                    stack.append((e, depth + 1))
            elif e.is_file() and e.name.endswith(suffixes):
                out.append(e)
    return out


def frontend_fingerprint() -> str:
    """前端产物指纹：dist 下 index.html + assets 文件。缺失返回 no-dist。"""
    if not (DIST / "index.html").is_file():
        return "no-dist"
    files = [DIST / "index.html"]
    assets = DIST / "assets"
    if assets.is_dir():
        files += _walk_files(assets, _DIST_SUFFIXES, max_depth=3)
    raw = "|".join(sorted(_file_fp(p) for p in files))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def backend_fingerprint() -> str:
    """后端源码指纹：ROOT + web 下的 .py 文件 + 前端 dist。"""
    parts = [ROOT.name]
    for root in (ROOT, WEB):
        for p in _walk_files(root, _PY_SUFFIXES, max_depth=3):
            parts.append(str(p.relative_to(ROOT)) + ":" + _file_fp(p))
    parts.append("frontend:" + frontend_fingerprint())
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def build_version() -> str:
    """对外暴露的版本号：backend 指纹前 12 位。"""
    return backend_fingerprint()[:12]


if __name__ == "__main__":
    print(f"backend_fingerprint: {backend_fingerprint()}")
    print(f"frontend_fingerprint: {frontend_fingerprint()}")
    print(f"build_version: {build_version()}")
