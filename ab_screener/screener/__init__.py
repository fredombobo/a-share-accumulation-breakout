"""
扫描内核包（V2R-A 拆分）
======================
职责单一：
  - data_loader   只读/标准化输入
  - prefilter     候选集合 + 理由
  - evaluator     单标的结果（阶梯/打分/软分/主题观察/信号检测）
  - orchestrator  进程/取消/进度/排序/聚合（run_scan 主体）

公共 facade 在 ab_screener/run_screener.py（CLI main + 兼容转发）。
"""
from __future__ import annotations

import os
import sys

# 与历史 run_screener 一致的导入环境（旧进程缓存/config 兼容）
_AB_SCREENER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AB_SCREENER not in sys.path:
    sys.path.insert(0, _AB_SCREENER)
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from ab_screener.screener.data_loader import load_market_data
from ab_screener.screener.evaluator import (
    _detect_on_codes,
    _score_codes,
    _soft_setup_row,
    _theme_soft_fill,
    apply_box_ladder,
    observed_signal,
)
from ab_screener.screener.orchestrator import run_scan
from ab_screener.screener.prefilter import prefilter

__all__ = [
    "_detect_on_codes",
    "_score_codes",
    "_soft_setup_row",
    "_theme_soft_fill",
    "apply_box_ladder",
    "load_market_data",
    "observed_signal",
    "prefilter",
    "run_scan",
]
