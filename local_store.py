"""薄 re-export：内容已迁至 ab_screener.local_store（G3 根脚本迁包）。

保留本文件以兼容旧导入路径 `from local_store import ...`（含下划线私有名）。
"""
import ab_screener.local_store as _m
from ab_screener.local_store import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
