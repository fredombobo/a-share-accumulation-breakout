"""类型化策略档案 StrategyProfile（配置真源）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StrategyProfile:
    profile_id: str
    name: str
    schema_version: int
    version: str
    status: str  # draft|candidate|active|retired
    # 箱体/突破
    box_min_days: int = 20
    box_max_days: int = 125
    box_max_amp: float = 0.26
    breakout_vol_ratio: float = 1.6
    breakout_chg_min: float = 0.02
    breakout_chg_max: float = 0.095
    breakout_vs_recent_vol_ratio: float = 1.3
    breakout_window_days: int = 5
    require_structure: bool = True
    # 标杆量出场
    vol_ratio_min: float = 1.5
    strong_reset: int = 3
    exit_window: int = 10
    stop_pct: float = 0.07
    target_pct: float = 0.12
    # 池
    top_n_trade: int = 20
    top_n_watch: int = 30
    notes: list[str] = field(default_factory=list)
    source_kind: str = "BUILT_IN"
    source_task_id: str | None = None
    source_param_id: str | None = None
    source_code_version: str | None = None
    source_dataset_version: str | None = None
    source_input_hash: str | None = None
    source_evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("strategy profile identity is required")
        if self.status not in {"draft", "candidate", "active", "retired"}:
            raise ValueError(f"unsupported strategy profile status: {self.status}")
        if self.box_min_days < 1 or self.box_max_days < self.box_min_days:
            raise ValueError("box day range is invalid")
        if self.breakout_window_days < 1:
            raise ValueError("breakout_window_days must be positive")
        if not 0 < self.box_max_amp < 1:
            raise ValueError("box_max_amp must be between 0 and 1")
        if not 0 <= self.breakout_chg_min < self.breakout_chg_max < 1:
            raise ValueError("breakout change range is invalid")
        if self.breakout_vol_ratio <= 0 or self.breakout_vs_recent_vol_ratio <= 0:
            raise ValueError("breakout volume ratios must be positive")
        if not 0 < self.stop_pct < 1:
            raise ValueError("stop_pct must be between 0 and 1")
        if not 0 < self.target_pct <= 1:
            raise ValueError("target_pct must be greater than 0 and at most 1")

    @property
    def is_default(self) -> bool:
        return self.profile_id == "default"

    def signal_kwargs(self) -> dict[str, Any]:
        """Return the exact technical entry contract shared with professional backtests."""
        return {
            "box_min_days": self.box_min_days,
            "box_max_days": self.box_max_days,
            "box_max_amp": self.box_max_amp,
            "breakout_vol_ratio": self.breakout_vol_ratio,
            "breakout_chg_min": self.breakout_chg_min,
            "breakout_chg_max": self.breakout_chg_max,
            "breakout_vs_recent_vol_ratio": self.breakout_vs_recent_vol_ratio,
            "breakout_window_days": self.breakout_window_days,
            "require_structure": self.require_structure,
        }

    def exit_params(self) -> dict[str, Any]:
        return {
            "vol_ratio_min": self.vol_ratio_min,
            "strong_reset": self.strong_reset,
            "exit_window": self.exit_window,
            "stop_pct": self.stop_pct,
            "target_pct": self.target_pct,
        }

    def required_scan_days(self) -> int:
        """Bars required by the daily detector, including recent breakout confirmation."""
        return max(160, self.box_max_days + self.breakout_window_days + 5)

    def to_canonical_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # schema v1/v2 档案创建时还没有止盈字段。保留其原始 canonical
        # 形态，保证不可变历史行的 config_hash 仍可验证；新档案统一使用 v3。
        if self.schema_version < 3:
            d.pop("target_pct", None)
        # 稳定排序
        return {k: d[k] for k in sorted(d.keys())}

    def config_hash(self) -> str:
        blob = json.dumps(self.to_canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), ensure_ascii=False, indent=2)


def default_profile() -> StrategyProfile:
    """从现有 config 常量构建默认档案（兼容）。"""
    try:
        import config as cfg

        return StrategyProfile(
            profile_id="default",
            name="default-accumulation-breakout",
            schema_version=3,
            version="1.1.0",
            status="active",
            box_min_days=int(getattr(cfg, "BOX_MIN_DAYS", 20)),
            box_max_days=int(getattr(cfg, "BOX_MAX_DAYS", 125)),
            box_max_amp=float(getattr(cfg, "BOX_MAX_AMP", 0.26)),
            breakout_vol_ratio=float(getattr(cfg, "BREAKOUT_VOL_RATIO", 1.6)),
            breakout_chg_min=float(getattr(cfg, "BREAKOUT_CHG_MIN", 0.02)),
            breakout_chg_max=float(getattr(cfg, "BREAKOUT_CHG_MAX", 0.095)),
            breakout_vs_recent_vol_ratio=float(
                getattr(cfg, "BREAKOUT_VS_RECENT_VOL_RATIO", 1.3)
            ),
            breakout_window_days=int(getattr(cfg, "BREAKOUT_WINDOW_DAYS", 5)),
            require_structure=True,
            vol_ratio_min=float(getattr(cfg, "BENCH_VOL_RATIO_MIN", 1.5)),
            strong_reset=int(getattr(cfg, "BENCH_STRONG_RESET", 3)),
            exit_window=int(getattr(cfg, "BENCH_EXIT_WINDOW", 10)),
            stop_pct=float(getattr(cfg, "BENCH_STOP_PCT", 0.07)),
            target_pct=float(getattr(cfg, "TARGET_PCT_1", 0.12)),
            top_n_trade=int(getattr(cfg, "TOP_N_TRADE", 20)),
            top_n_watch=int(getattr(cfg, "TOP_N_WATCH", 30)),
        )
    except Exception:  # noqa: BLE001
        return StrategyProfile(
            profile_id="default",
            name="default-accumulation-breakout",
            schema_version=3,
            version="1.1.0",
            status="active",
        )


def load_profile_json(path: str | Path) -> StrategyProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return strategy_profile_from_dict(data)


def strategy_profile_from_dict(data: dict[str, Any]) -> StrategyProfile:
    if not isinstance(data, dict):
        raise TypeError("strategy profile must be an object")
    allowed = StrategyProfile.__dataclass_fields__
    values = {key: data[key] for key in allowed if key in data}
    required = {"profile_id", "name", "schema_version", "version", "status"}
    missing = sorted(required - set(values))
    if missing:
        raise ValueError(f"strategy profile missing fields: {', '.join(missing)}")
    return StrategyProfile(**values)


def save_default_profile(path: str | Path) -> StrategyProfile:
    p = default_profile()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(p.to_json(), encoding="utf-8")
    return p
