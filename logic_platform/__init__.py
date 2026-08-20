"""量价预测 · 逻辑生成平台（logic_platform）。

挂在 accumulation_breakout 上的扩展包，规格见
docs/VOLUME-PRICE-LOGIC-PLATFORM.md。默认 research_only，所有生成逻辑
必须经 DSL + 回测闸门后才可进入纸交易。
"""
from __future__ import annotations

__version__ = "0.2.0"
FEATURE_VERSION = "v0.2.0"
