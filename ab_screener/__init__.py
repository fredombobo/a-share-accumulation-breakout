"""accumulation_breakout 领域包（upgrade system 架构）。

包结构：
  domain      信号/扫描/成本/配置/错误
  data        SQLite repository / 分区版本 / Parquet 缓存
  research    回测/基线/OOS/晋级
  application 编排/查询/持久任务
  api         FastAPI routers
  jobs        独立扫描 Worker
"""
from __future__ import annotations

__version__ = "2.0.0"
ENGINE_VERSION = "v2"
