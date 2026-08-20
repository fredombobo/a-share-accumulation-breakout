"""统一错误码。"""
from __future__ import annotations


class DomainError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class FailClosedError(DomainError):
    """研究/晋级 fail-closed：降级模式下禁止宣称 edge 或写 active。"""

    def __init__(self, message: str, **kw):
        super().__init__("FAIL_CLOSED", message, **kw)


class CancelledError(DomainError):
    def __init__(self, message: str = "任务已取消"):
        super().__init__("CANCELLED", message)
