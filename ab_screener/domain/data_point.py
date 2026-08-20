"""PIT 数据点契约：业务键 + revision + available_at + source + content_hash 五元组。

契约（implementation P1.1）：
- 所有历史数据以 append-only 记录保存；同一业务键的多次修订通过 revision 区分。
- `available_at`（+08:00，数据可用时刻）决定 `decision_at` 时刻应读取哪个版本：
  对同一业务键，取 `available_at <= decision_at` 中 revision 最大的一条。
- 缺 `available_at/source/revision` 或时间无时区的记录一律拒绝（fail-closed）；
  回填数据不得伪装成历史可用（available_at 必须为真实入库时刻，非数据日期）。
- `content_hash` = 规范化 payload 的 SHA-256（前 16 位），用于覆盖/完整性核对。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TZ_SH = ZoneInfo("Asia/Shanghai")


def normalize_ts(value: Any) -> str:
    """强制转换为带 +08:00 偏移的 ISO-8601 字符串；无时区时间按 Asia/Shanghai 解释。"""
    if value is None or value == "":
        raise ValueError("时间字段缺失，拒绝写入 PIT")
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ_SH)
        return parsed.astimezone(TZ_SH).isoformat(timespec="seconds")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ_SH)
        return value.astimezone(TZ_SH).isoformat(timespec="seconds")
    raise ValueError(f"无法解析时间字段: {value!r}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash_for(payload: dict[str, Any]) -> str:
    """规范化 payload 的 SHA-256（16 位）。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PitRecord:
    """PIT 五元组：业务键标识一条事实，revision 标识它的第几次修订。"""

    business_key: dict[str, str]  # 如 {"ts_code": "...", "trade_date": "..."}（字符串化）
    revision: int
    available_at: str  # +08:00 ISO；数据真实可用时刻
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.business_key, dict) or not self.business_key:
            raise ValueError("PIT 记录缺少业务键")
        if not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError(f"PIT 记录 revision 必须为正整数: {self.revision!r}")
        # available_at/source 缺失 → 拒绝（fail-closed）
        if not self.available_at or not str(self.available_at).strip():
            raise ValueError("PIT 记录缺少 available_at")
        if not self.source or not str(self.source).strip():
            raise ValueError("PIT 记录缺少 source")
        # 校验时间可解析且带时区（写入前统一 +08:00）
        normalized = normalize_ts(self.available_at)
        object.__setattr__(self, "available_at", normalized)
        if not self.content_hash:
            object.__setattr__(self, "content_hash", content_hash_for(self.payload))


def record_valid_at(record: PitRecord, decision_at: Any) -> bool:
    """record 在 decision_at 时刻是否可用：available_at <= decision_at。"""
    return normalize_ts(record.available_at) <= normalize_ts(decision_at)


def select_asof(records: list[PitRecord], decision_at: Any) -> PitRecord | None:
    """同一业务键的修订列表中，decision_at 时刻应读取的版本（revision 最大者）。"""
    usable = [r for r in records if record_valid_at(r, decision_at)]
    if not usable:
        return None
    return max(usable, key=lambda r: r.revision)
