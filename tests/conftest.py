"""Hermetic process-wide database for tests importing the assembled Web app.

Several contract tests import ``web.backend_app`` at collection time.  The Web
entry point deliberately refuses an unmigrated database, so collection must not
depend on whichever ignored ``runtime/stock_data.db`` happens to exist in a Git
worktree.  Build a disposable, fully migrated database before test modules are
imported and route the assembled app to it through the production dependency
injection setting.

Individual tests remain free to monkeypatch their own database paths.  This
bootstrap never opens or migrates the developer's runtime database.
"""
from __future__ import annotations

import atexit
import os
import sqlite3
import tempfile
from pathlib import Path

from ab_screener.data.migration_intents import register_lhb_intents
from ab_screener.data.migration_registry import apply_pending
from local_store import LocalStore

# joblib/loky probes physical cores through PowerShell on Windows.  On GBK
# hosts that subprocess can emit undecodable output in a background reader.
# A fixed test ceiling avoids the external probe and keeps resource use stable.
os.environ["LOKY_MAX_CPU_COUNT"] = "1"

_TEST_DB_DIR = tempfile.TemporaryDirectory(
    prefix="ab-screener-pytest-", ignore_cleanup_errors=True
)
_TEST_DB_PATH = Path(_TEST_DB_DIR.name) / "stock_data.db"
os.environ["AB_DB_PATH"] = str(_TEST_DB_PATH)

# LocalStore creates the legacy/core tables; the unified runner then records all
# registered v2 migration identities and makes the startup schema assertion pass.
# 测试用的是一次性临时库，把龙虎榜意图也注册上，让 LHB 测试拿到完整 schema。
# 生产库不受影响：那条路径永远不会调用 register_lhb_intents()。
register_lhb_intents()
LocalStore(db_path=_TEST_DB_PATH)
with sqlite3.connect(str(_TEST_DB_PATH), timeout=30) as _conn:
    apply_pending(_conn)


@atexit.register
def _cleanup_test_database() -> None:
    _TEST_DB_DIR.cleanup()
