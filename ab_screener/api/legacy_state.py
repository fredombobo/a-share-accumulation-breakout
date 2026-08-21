"""legacy backend_app 共享状态（G2 拆路由第一步：状态集中，路由搬家）。

只持有模块级单例 / 缓存 / 任务字典 / 锁；不定义路由、不做 IO。
- 路径基准：本文件在 `ab_screener/api/`，`parents[2]` = 项目根（与 backend_app 的
  `_PARENT = Path(__file__).parent.parent` 等价）。
- 迁移时状态对象引用不变（只搬家，不复制），后台线程语义保持一致。
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from build_version import build_version as _compute_build_version
from local_store import LocalStore
from ab_screener.research.store import ResearchRunStore

_PARENT = Path(__file__).resolve().parents[2]  # ab_screener/api → 项目根
_DB = _PARENT / "runtime" / "stock_data.db"

_BUILD_VERSION = _compute_build_version()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")
_INSTANCE_ID = uuid.uuid4().hex[:12]
_LOGGER = logging.getLogger(__name__)

_store = LocalStore()

_SECTOR_FLOW_CACHE: dict = {}  # {(days, data_version): (dates, pivot_df)}
_SIG_CACHE: dict = {}          # {(ts_code, as_of): sig} 个股信号缓存

_OVERVIEW_CACHE: dict = {"key": None, "payload": None}   # key=(as_of, pool)
_SCAN_RESULT_CACHE: dict = {"key": None, "df": None}     # key=max(trade_date)
_DATES_CACHE: dict = {"key": None, "dates": None}        # key=max(trade_date) 全量日期

_SCAN_TASKS: dict[str, dict] = {}
_SCAN_CANCEL_EVENTS: dict[str, threading.Event] = {}
_SCAN_LOCK = threading.Lock()
_SCAN_TASKS_MAX = 20          # 历史任务保留上限
_SECTOR_FLOW_CACHE_MAX = 6    # 板块资金流缓存条目上限

_LAB_TASKS: dict[str, dict] = {}
_LAB_LOCK = threading.Lock()
_LAB_TASKS_MAX = 10
_LAB_STORE = ResearchRunStore(_store.db_path)

_SYNC_LOCK = threading.Lock()
_SYNC_STATE: dict = {
    "status": "idle",  # idle | running | done | error
    "message": "",
    "started_at": None,
    "finished_at": None,
    "latest_daily": None,
    "latest_moneyflow": None,
    "failed_dates": [],
}

_BT_LOCK = threading.Lock()
_BT_TASKS: dict[str, dict] = {}
_BT_TASKS_MAX = 20
