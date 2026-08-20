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
    # 标杆量出场
    vol_ratio_min: float = 1.5
    strong_reset: int = 3
    exit_window: int = 10
    stop_pct: float = 0.07
    # 池
    top_n_trade: int = 20
    top_n_watch: int = 30
    notes: list[str] = field(default_factory=list)

    def to_canonical_dict(self) -> dict[str, Any]:
        d = asdict(self)
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
            schema_version=1,
            version="1.0.0",
            status="active",
            box_min_days=int(getattr(cfg, "BOX_MIN_DAYS", 20)),
            box_max_days=int(getattr(cfg, "BOX_MAX_DAYS", 125)),
            box_max_amp=float(getattr(cfg, "BOX_MAX_AMP", 0.26)),
            breakout_vol_ratio=float(getattr(cfg, "BREAKOUT_VOL_RATIO", 1.6)),
            breakout_chg_min=float(getattr(cfg, "BREAKOUT_CHG_MIN", 0.02)),
            breakout_chg_max=float(getattr(cfg, "BREAKOUT_CHG_MAX", 0.095)),
            vol_ratio_min=float(getattr(cfg, "BENCH_VOL_RATIO_MIN", 1.5)),
            strong_reset=int(getattr(cfg, "BENCH_STRONG_RESET", 3)),
            exit_window=int(getattr(cfg, "BENCH_EXIT_WINDOW", 10)),
            stop_pct=float(getattr(cfg, "BENCH_STOP_PCT", 0.07)),
            top_n_trade=int(getattr(cfg, "TOP_N_TRADE", 20)),
            top_n_watch=int(getattr(cfg, "TOP_N_WATCH", 30)),
        )
    except Exception:  # noqa: BLE001
        return StrategyProfile(
            profile_id="default",
            name="default-accumulation-breakout",
            schema_version=1,
            version="1.0.0",
            status="active",
        )


def load_profile_json(path: str | Path) -> StrategyProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return StrategyProfile(**{k: data[k] for k in StrategyProfile.__dataclass_fields__ if k in data})


def save_default_profile(path: str | Path) -> StrategyProfile:
    p = default_profile()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(p.to_json(), encoding="utf-8")
    return p
