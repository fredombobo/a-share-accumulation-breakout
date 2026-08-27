"""薄 re-export：内容已迁至 ab_screener.signals（G3 根脚本迁包）。

保留本文件以兼容旧导入路径 `from signals import ...`（含下划线私有名）。
"""
import ab_screener.signals as _m
from ab_screener.signals import *

# See local_store.py: make the public compatibility contract statically
# discoverable while preserving the complete runtime re-export below.
detect_accumulation_breakout = _m.detect_accumulation_breakout

globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
