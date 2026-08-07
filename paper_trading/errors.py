"""领域错误：统一结构化错误响应（code/message/details/retryable）。"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """纸面交易领域错误。API 层捕获后返回：

    {"code": ..., "message": ..., "details": {...}, "retryable": false}
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


# 常用错误码
ERR_UNKNOWN_ACCOUNT = "ACCOUNT_NOT_FOUND"
ERR_ACCOUNT_EXISTS = "ACCOUNT_ALREADY_EXISTS"
ERR_INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
ERR_INSUFFICIENT_SELLABLE = "INSUFFICIENT_SELLABLE_QUANTITY"
ERR_INVALID_STATE = "INVALID_ORDER_STATE"
ERR_DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
ERR_UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT_RULE"
ERR_STALE_QUOTE = "STALE_QUOTE"
ERR_MARKET_CLOSED = "MARKET_CLOSED"
ERR_UNPROCESSED_CORPORATE_ACTION = "UNPROCESSED_CORPORATE_ACTION"
ERR_POSITIVE_CASH = "NEGATIVE_CASH_FORBIDDEN"
ERR_DATA_GATE = "REAL_DATA_GATE_FAILED"
