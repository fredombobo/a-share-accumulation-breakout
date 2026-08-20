"""架构边界静态检查：API 装配层不得直接 import sqlite3/subprocess。

契约（implementation P0.3 / G1）：API→application→domain/data 单向依赖；
API 层直接导入 sqlite3/subprocess 视为架构违规。

G1：覆盖 web/backend_app.py 与 ab_screener/api/**；存量白名单必须为空。
--strict 与默认行为相同（无债务可宽限）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IN_API = {"sqlite3", "subprocess"}

API_FILES = [
    ROOT / "web" / "backend_app.py",
    *((ROOT / "ab_screener" / "api").rglob("*.py")),
]

STALE_ALLOWLIST: dict[str, set[str]] = {}


def check_imports(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return [f"语法错误: {path}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_IN_API:
                    violations.append(f"{path.relative_to(ROOT)}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in FORBIDDEN_IN_API:
                violations.append(f"{path.relative_to(ROOT)}: from {node.module} import ...")
    return violations


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv

    debt: list[str] = []
    problems: list[str] = []
    for path in API_FILES:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        allowed = STALE_ALLOWLIST.get(rel, set())
        for v in check_imports(path):
            module = v.rsplit(" ", 1)[-1].split(".")[0]
            if module in allowed:
                debt.append(v)
            else:
                problems.append(v)

    if debt:
        print("架构边界已知债务:")
        for d in sorted(set(debt)):
            print(" -", d)
    if problems:
        print("架构边界违规（必须修复）:")
        for p in sorted(set(problems)):
            print(" -", p)
        return 1
    if strict and debt:
        print("--strict: 存量债务未清零，视为失败")
        return 1
    print(
        "architecture OK: 无 sqlite3/subprocess 直接 import"
        + (f"（存量债务 {len(set(debt))} 项）" if debt else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
