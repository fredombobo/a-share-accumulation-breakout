"""薄 re-export：内容已迁至 ab_screener.market_regime（G3 根脚本迁包）。

保留本文件以兼容旧导入路径 `from market_regime import ...`（含下划线私有名）。
"""
import ab_screener.market_regime as _m
from ab_screener.market_regime import *

globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
