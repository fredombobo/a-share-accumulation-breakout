"""v2 错误码注册表与统一错误 envelope。

契约（platform-contracts §6.3）：错误只能从本 registry 选择；
前端默认展示「原因+解决方式」，技术 details 折叠。

envelope:
  {"code": "...", "message": "人话", "details": {}, "retryable": bool, "request_id": "..."}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 错误码唯一注册（新增 code 必须在此登记） ──
ERROR_CODES: dict[str, dict[str, Any]] = {
    # 输入/契约
    "VALIDATION_FAILED": {"message": "输入校验失败", "retryable": False},
    "UNKNOWN_ERROR_CODE": {"message": "未知错误码", "retryable": False},
    # 时点/PIT
    "DATA_AVAILABLE_AFTER_DECISION": {"message": "该数据在决策时尚不可用", "retryable": False},
    "SNAPSHOT_DECISION_CONFLICT": {"message": "快照与决策时点冲突", "retryable": False},
    "MISSING_DECISION_AT": {"message": "缺少 decision_at 查询时点", "retryable": False},
    # 幂等
    "IDEMPOTENCY_KEY_REUSED": {"message": "幂等键已被不同请求使用", "retryable": False},
    "IDEMPOTENCY_KEY_MISSING": {"message": "写操作缺少 Idempotency-Key", "retryable": False},
    # 资源
    "NOT_FOUND": {"message": "资源不存在", "retryable": False},
    "CONFLICT": {"message": "资源冲突", "retryable": False},
    "ALREADY_EXISTS": {"message": "资源已存在", "retryable": False},
    "FEATURE_DISABLED": {"message": "该功能当前未启用", "retryable": False},
    # 研究/门禁
    "RESEARCH_MODE_NOT_FULL": {"message": "研究模式非 full，禁止 edge 话术", "retryable": False},
    "EVIDENCE_INSUFFICIENT": {"message": "证据不足", "retryable": False},
    # 组合风控
    "CONSTRAINT_VIOLATION": {"message": "组合约束违规", "retryable": False},
    "LIVE_TRADING_ENABLED": {"message": "实盘开关被打开，启动必须失败", "retryable": False},
    # 数据
    "DB_QUICK_CHECK_FAILED": {"message": "数据库完整性检查失败", "retryable": True},
    "SCHEMA_INCOMPATIBLE": {"message": "数据库 schema 与代码不兼容，请先迁移", "retryable": False},
    "MIGRATION_REQUIRED": {"message": "存在未应用的迁移", "retryable": False},
    # 运维
    "SCHEDULER_BUSY": {"message": "调度器忙", "retryable": True},
    "BACKUP_STALE": {"message": "备份过期", "retryable": False},
    # 通用
    "INTERNAL_ERROR": {"message": "内部错误", "retryable": True},
}


@dataclass(frozen=True)
class V2Error(Exception):
    """v2 统一业务错误；可直接序列化为契约 envelope。"""

    code: str
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool | None = None
    request_id: str | None = None
    http_status: int = 400

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"错误码未注册: {self.code}（请在 ERROR_CODES 登记）")
        registered = ERROR_CODES[self.code]
        # 注册表消息为默认；实例可覆盖
        if self.message is None:
            object.__setattr__(self, "message", str(registered["message"]))
        if self.retryable is None:
            object.__setattr__(self, "retryable", bool(registered["retryable"]))

    def to_envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "request_id": self.request_id or "",
        }


def assert_code_registered(code: str) -> None:
    """registry 一致性硬断言：引用未知错误码直接失败（fail-closed）。"""
    if code not in ERROR_CODES:
        raise ValueError(f"引用了未注册错误码: {code}")


def error_codes_manifest() -> dict[str, Any]:
    """错误码注册表机器快照（P0 契约文件 error-codes-v2.json 的数据源）。"""
    return {code: dict(meta) for code, meta in ERROR_CODES.items()}
