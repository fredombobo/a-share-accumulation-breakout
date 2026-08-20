"""logic_platform 配置。

宿主 config.py 是纯常量模块，本包自建独立配置，支持环境变量覆盖：
  - LOGIC_PLATFORM_ENABLED  : 功能开关（true/false）
  - LOGIC_LAKE_ROOT         : 888 data_lake 路径覆盖
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_LAKE_ROOT = r"C:\Users\13818\888\data_lake"


@dataclass(frozen=True)
class LogicConfig:
    enabled: bool = True
    lake_root: str = _DEFAULT_LAKE_ROOT
    lake_readonly: bool = True
    feature_materialize: bool = True
    default_horizon_days: tuple[int, ...] = (5, 10, 20)
    require_gate_for_paper: bool = True
    research_only_default: bool = True


def get_config() -> LogicConfig:
    """读取环境变量并返回配置。"""
    enabled = os.environ.get("LOGIC_PLATFORM_ENABLED", "true").lower() == "true"
    lake_root = os.environ.get("LOGIC_LAKE_ROOT", _DEFAULT_LAKE_ROOT)
    return LogicConfig(enabled=enabled, lake_root=Path(lake_root).as_posix())
