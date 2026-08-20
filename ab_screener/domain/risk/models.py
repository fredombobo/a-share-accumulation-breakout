"""组合风险领域：模型与稳定拒绝码（P5.1）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 稳定拒绝码注册表（P5.1 验收：独立稳定拒绝码）
RISK_CODES: tuple[str, ...] = (
    "RISK_CASH_INSUFFICIENT",       # 现金
    "RISK_LOT_SHARE",               # 份额（非整手/非整数）
    "RISK_T1_SELLABLE",             # T+1 可卖
    "RISK_SINGLE_NAME_LIMIT",       # 单票
    "RISK_INDUSTRY_LIMIT",          # 行业
    "RISK_THEME_LIMIT",             # 主题
    "RISK_CORRELATED_EXPOSURE",     # 相似暴露
    "RISK_POSITION_COUNT_LIMIT",    # 持仓数
    "RISK_TOTAL_POSITION_LIMIT",    # 总仓
    "RISK_MIN_CASH",                # 最低现金
    "RISK_DAILY_ADDITION_LIMIT",    # 单日新增
    "RISK_PARTICIPATION_LIMIT",     # 参与率
    "RISK_PRICE_DEVIATION",         # 价格偏离
    "RISK_DEFENSIVE_REGIME",        # 防守环境
    "RISK_STALE_DATA",              # 陈旧数据
)

RISK_CODE_SET = set(RISK_CODES)


@dataclass(frozen=True)
class ConstraintViolation:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in RISK_CODE_SET:
            raise ValueError(f"非法风险拒绝码: {self.code}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class Position:
    ts_code: str
    qty: int
    cost_price_micro: int = 0
    sellable_qty: int = 0
    industry: str = ""
    theme: str = ""
    corr_group: str = ""
    latest_close_micro: int = 0


@dataclass(frozen=True)
class PortfolioState:
    cash_fen: int
    equity_fen: int
    positions: tuple[Position, ...] = ()
    today: str = ""
    trade_date: str = ""
    regime: str = "neutral"          # defensive / neutral / aggressive
    data_fresh_as_of: str = ""       # 数据最新日期（陈旧数据判定）

    def qty_of(self, ts_code: str) -> int:
        for p in self.positions:
            if p.ts_code == ts_code:
                return p.qty
        return 0

    def sellable_of(self, ts_code: str) -> int:
        for p in self.positions:
            if p.ts_code == ts_code:
                return p.sellable_qty
        return 0

    def market_value_fen(self) -> int:
        total = 0
        for p in self.positions:
            if p.latest_close_micro > 0:
                total += p.latest_close_micro * p.qty // 10_000
        return total


@dataclass(frozen=True)
class OrderIntent:
    ts_code: str
    side: str            # BUY / SELL
    qty: int
    price_micro: int
    participation_bps: int = 500
    expected_quote_available_at: str = ""   # 价格偏离/陈旧数据用
    strategy: str = ""


@dataclass(frozen=True)
class RiskConfig:
    """阈值配置（由 robust_personal_v2.yaml 冻结）。"""

    max_single_name_pct: float = 0.30
    max_industry_pct: float = 0.40
    max_theme_pct: float = 0.40
    max_corr_group_pct: float = 0.50
    max_position_count: int = 30
    max_total_position_pct: float = 0.90
    min_cash_pct: float = 0.10
    max_daily_addition_pct: float = 0.10
    max_price_deviation_pct: float = 0.03
    stale_data_days: int = 1
    lot_size: int = 100
    participation_cap_bps: int = 500
