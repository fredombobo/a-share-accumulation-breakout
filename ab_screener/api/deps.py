"""v2 API 依赖注入：DB 路径解析（P7.1 装配层契约）。

- 优先级：环境变量 `AB_DB_PATH`（绝对路径）→ 项目默认 `runtime/stock_data.db`。
- 只读不写：禁止在 API 层解析后直接修改配置。
- 架构契约：API 层不得直接 import sqlite3/subprocess（见 scripts/check_architecture.py）；
  db_path 仅作为依赖注入给 domain/application 层使用。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # deps.py → api → ab_screener → 项目根
DEFAULT_DB_PATH = ROOT / "runtime" / "stock_data.db"


def default_db_path() -> Path:
    env = os.environ.get("AB_DB_PATH")
    if env:
        p = Path(env)
        if not p.is_absolute():
            raise ValueError(f"AB_DB_PATH 必须是绝对路径（防误操作）: {env!r}")
        return p
    return DEFAULT_DB_PATH


def get_db_path() -> str:
    """FastAPI 依赖：返回运行库绝对路径字符串。"""
    return str(default_db_path())
