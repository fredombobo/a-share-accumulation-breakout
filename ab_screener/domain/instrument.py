"""instrument 领域：证券分类 + 上市/退市生命周期 + 校验 fail-closed。

契约（implementation P1.2）：
- 首版个股宇宙只含 A 股股票（stock）；指数/ETF/基金/债券/北交所/孤儿代码不进入。
- 生命周期：有效期为 [list_date, delist_date)；退市日当天起不再进入宇宙（保守口径）。
- 缺 ts_code/list_date/security_type 的规则一律拒绝（fail-closed）；
  缺 instrument rule 的回测/订单必须显式失败，不使用全市场默认值兜底。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _num(ts_code: str) -> str:
    return (ts_code or "").split(".")[0]


def classify_security(ts_code: str) -> str:
    """按代码段分类：stock / index / etf / fund / bond / bse / other。"""
    code = (ts_code or "").strip().upper()
    if not code or "." not in code:
        return "other"
    num = code.split(".")[0]
    suffix = code.split(".")[1]
    if suffix == "BJ" or num.startswith(("4", "8", "92")):
        return "bse"
    if suffix == "SH":
        if num.startswith(("600", "601", "603", "605", "688")):
            return "stock"
        if num.startswith(("000", "900")):
            return "index"
        if num.startswith(("51", "56", "58")):
            return "etf"
        if num.startswith(("50", "52", "53", "55")):
            return "fund"
        if num.startswith(("11", "12", "13", "18")):
            return "bond"
        return "other"
    if suffix == "SZ":
        if num.startswith(("000", "001", "002", "003", "300", "301")):
            return "stock"
        if num.startswith("399"):
            return "index"
        if num.startswith(("15", "16")):
            return "etf"
        if num.startswith(("10", "12", "13", "14", "18")):
            return "bond"
        return "other"
    return "other"


def is_a_share_stock(ts_code: str) -> bool:
    return classify_security(ts_code) == "stock"


@dataclass(frozen=True)
class Instrument:
    """一只证券的注册规则：类型 + 生命周期。"""

    ts_code: str
    name: str
    exchange: str            # SSE / SZSE / BSE
    security_type: str       # stock / index / etf / fund / bond / bse / other
    list_date: str           # YYYYMMDD
    delist_date: str | None = None  # YYYYMMDD；None = 仍在市
    source: str = "tushare"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ts_code or not str(self.ts_code).strip():
            raise ValueError("instrument 规则缺少 ts_code")
        if not self.list_date or not str(self.list_date).strip():
            raise ValueError(f"instrument 规则缺少 list_date: {self.ts_code}")
        if not self.security_type or not str(self.security_type).strip():
            raise ValueError(f"instrument 规则缺少 security_type: {self.ts_code}")
        if self.delist_date and str(self.delist_date).strip() == "":
            object.__setattr__(self, "delist_date", None)
        if self.delist_date and self.delist_date < self.list_date:
            raise ValueError(
                f"instrument 生命周期非法: {self.ts_code} delist {self.delist_date} < list {self.list_date}"
            )

    def is_active_at(self, trade_date: str) -> bool:
        """有效期 [list_date, delist_date)；退市日当天起不再进入宇宙。"""
        if not trade_date or trade_date < self.list_date:
            return False
        return not (self.delist_date and trade_date >= self.delist_date)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "exchange": self.exchange,
            "security_type": self.security_type,
            "list_date": self.list_date,
            "delist_date": self.delist_date,
            "source": self.source,
            "extra": dict(self.extra),
        }
