"""龙虎榜数据源适配：Tushare + 官方源 fail-closed。

CI / 单测只走注入的 fake client。真实 Tushare 只经 `tushare_init.pro`。
官方交易所默认拒绝抓取，禁止绕过授权或验证码。Token 与完整原始响应不进结果对象。
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.adapters.tushare_pit import (
    get_pro_handle,
    prepare_hm_list_records,
    prepare_top_inst_records,
    prepare_top_list_records,
)
from ab_screener.domain.data_point import canonical_json, normalize_ts
from ab_screener.domain.lhb_contracts import SOURCE_STATUS_VALUES, parse_enum

MAX_ATTEMPTS = 3
CIRCUIT_THRESHOLD = 5
_TZ = ZoneInfo("Asia/Shanghai")


def _default_now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")
OFFICIAL_FAIL_REASON = (
    "OFFICIAL_FETCH_NOT_AUTHORIZED: fail-closed; no scrape, captcha, or anti-bot bypass"
)


class LhbSourceError(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class LhbNotPublished(LhbSourceError):
    def __init__(self, reason: str = "NOT_PUBLISHED"):
        super().__init__("NOT_PUBLISHED", reason)


class LhbRateLimited(LhbSourceError):
    def __init__(self, reason: str = "RATE_LIMITED"):
        super().__init__("FETCH_FAILED", reason)


class LhbTimeout(LhbSourceError):
    def __init__(self, reason: str = "TIMEOUT"):
        super().__init__("FETCH_FAILED", reason)


class LhbHtmlChanged(LhbSourceError):
    def __init__(self, reason: str = "HTML_STRUCTURE_CHANGED"):
        super().__init__("FETCH_FAILED", reason)


class LhbMissingField(LhbSourceError):
    def __init__(self, reason: str = "MISSING_FIELD"):
        super().__init__("FETCH_FAILED", reason)


@dataclass(frozen=True)
class FetchResult:
    source: str
    dataset: str
    partition_key: str
    source_status: str
    rows: tuple[dict[str, Any], ...]
    row_count: int
    content_sha256: str
    available_at: str
    error_reason: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parse_enum(self.source_status, SOURCE_STATUS_VALUES, label="source_status")
        if "token" in canonical_json(self.summary).lower():
            raise ValueError("FetchResult.summary 禁止包含 token")


def rows_content_sha256(rows: list[dict[str, Any]]) -> str:
    blob = "\n".join(sorted(canonical_json(row) for row in rows))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _records(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "empty") and df.empty:
        return []
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    if isinstance(df, list):
        return [dict(item) for item in df]
    raise LhbMissingField("UNSUPPORTED_PAYLOAD")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns: list[str] = []
    if rows:
        columns = sorted({str(k) for row in rows for k in row})
    return {"row_count": len(rows), "columns": columns}


class CircuitBreaker:
    def __init__(self, threshold: int = CIRCUIT_THRESHOLD):
        self.threshold = threshold
        self.failures = 0
        self.open = False

    def allow(self) -> bool:
        return not self.open

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open = True

    def ok(self) -> None:
        self.failures = 0
        self.open = False


def _retry_call(
    fn: Callable[[], list[dict[str, Any]]],
    *,
    attempts: int,
    sleeper: Callable[[float], None],
) -> list[dict[str, Any]]:
    last: LhbSourceError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except LhbNotPublished:
            raise
        except (LhbRateLimited, LhbTimeout, LhbHtmlChanged, LhbMissingField, LhbSourceError) as exc:
            last = exc
            if attempt >= attempts:
                break
            sleeper(min(2 ** (attempt - 1), 8))
        except Exception as exc:  # noqa: BLE001
            last = LhbSourceError("FETCH_FAILED", f"{type(exc).__name__}: {exc}")
            if attempt >= attempts:
                break
            sleeper(min(2 ** (attempt - 1), 8))
    assert last is not None
    raise last


class TushareLhbAdapter:
    """Tushare 龙虎榜适配器。pro=None 时走根 tushare_init。"""

    source = "tushare"

    def __init__(
        self,
        pro: Any | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        sleeper: Callable[[float], None] | None = None,
        breaker: CircuitBreaker | None = None,
        now_iso: Callable[[], str] | None = None,
    ) -> None:
        self._pro = pro
        self.max_attempts = max_attempts
        self._sleeper = sleeper or time.sleep
        self.breaker = breaker or CircuitBreaker()
        self._now_iso = now_iso or _default_now

    def fetch(
        self,
        dataset: str,
        trade_date: str,
        *,
        published: bool | None = None,
    ) -> FetchResult:
        if dataset not in {"top_list", "top_inst", "hm_list"}:
            raise ValueError(f"不支持的数据集: {dataset}")
        if not self.breaker.allow():
            return self._failed(dataset, trade_date, "CIRCUIT_OPEN")
        try:
            rows = _retry_call(
                lambda: self._pull(dataset, trade_date),
                attempts=self.max_attempts,
                sleeper=self._sleeper,
            )
        except LhbNotPublished as exc:
            self.breaker.ok()
            return self._result(dataset, trade_date, "NOT_PUBLISHED", [], exc.reason)
        except LhbSourceError as exc:
            self.breaker.fail()
            return self._failed(dataset, trade_date, exc.reason)
        except Exception as exc:  # noqa: BLE001
            self.breaker.fail()
            return self._failed(dataset, trade_date, f"{type(exc).__name__}: {exc}")
        self.breaker.ok()
        if not rows:
            if published is True:
                return self._result(dataset, trade_date, "VALID_EMPTY", [], None)
            return self._result(dataset, trade_date, "NOT_PUBLISHED", [], "EMPTY_WITHOUT_PUBLISHED_FLAG")
        return self._result(dataset, trade_date, "COMPLETE", rows, None)

    def _pull(self, dataset: str, trade_date: str) -> list[dict[str, Any]]:
        handle = get_pro_handle(self._pro)
        if dataset == "top_list":
            raw = _records(handle.top_list(trade_date=trade_date))
            try:
                return prepare_top_list_records(raw)
            except ValueError as exc:
                raise LhbMissingField(str(exc)) from exc
        if dataset == "top_inst":
            raw = _records(handle.top_inst(trade_date=trade_date))
            try:
                return prepare_top_inst_records(raw)
            except ValueError as exc:
                raise LhbMissingField(str(exc)) from exc
        raw = _records(handle.hm_list())
        try:
            return prepare_hm_list_records(raw, list_date=trade_date)
        except ValueError as exc:
            raise LhbMissingField(str(exc)) from exc

    def _failed(self, dataset: str, trade_date: str, reason: str) -> FetchResult:
        return self._result(dataset, trade_date, "FETCH_FAILED", [], reason)

    def _result(
        self,
        dataset: str,
        trade_date: str,
        status: str,
        rows: list[dict[str, Any]],
        error_reason: str | None,
    ) -> FetchResult:
        return FetchResult(
            source=self.source,
            dataset=dataset,
            partition_key=trade_date,
            source_status=status,
            rows=tuple(rows),
            row_count=len(rows),
            content_sha256=rows_content_sha256(rows),
            available_at=normalize_ts(self._now_iso()),
            error_reason=error_reason,
            summary=_summary(rows),
        )


class OfficialExchangeAdapter:
    """上交所 / 深交所公开信息。未注入获准客户端时 fail-closed。"""

    def __init__(self, exchange: str, client: Any | None = None) -> None:
        if exchange not in {"SH", "SZ"}:
            raise ValueError(f"未知交易所: {exchange}")
        self.exchange = exchange
        self.source = f"official_{exchange.lower()}"
        self._client = client

    def fetch(self, dataset: str, trade_date: str) -> FetchResult:
        available = normalize_ts(_default_now())
        if self._client is None:
            return FetchResult(
                source=self.source,
                dataset=dataset,
                partition_key=trade_date,
                source_status="FETCH_FAILED",
                rows=(),
                row_count=0,
                content_sha256=rows_content_sha256([]),
                available_at=available,
                error_reason=OFFICIAL_FAIL_REASON,
                summary={"row_count": 0, "columns": [], "exchange": self.exchange},
            )
        try:
            payload = self._client.fetch(dataset=dataset, trade_date=trade_date, exchange=self.exchange)
            rows = list(payload.get("rows") or [])
            status = str(payload.get("source_status") or "COMPLETE")
            reason = payload.get("error_reason")
        except (LhbTimeout, LhbHtmlChanged, LhbMissingField, LhbRateLimited, LhbNotPublished) as exc:
            return FetchResult(
                source=self.source,
                dataset=dataset,
                partition_key=trade_date,
                source_status=exc.code if exc.code in SOURCE_STATUS_VALUES else "FETCH_FAILED",
                rows=(),
                row_count=0,
                content_sha256=rows_content_sha256([]),
                available_at=available,
                error_reason=exc.reason,
                summary={"row_count": 0, "columns": [], "exchange": self.exchange},
            )
        return FetchResult(
            source=self.source,
            dataset=dataset,
            partition_key=trade_date,
            source_status=status,
            rows=tuple(rows),
            row_count=len(rows),
            content_sha256=rows_content_sha256(rows),
            available_at=available,
            error_reason=reason,
            summary=_summary(rows),
        )


def with_fallback(primary: FetchResult, secondary: FetchResult) -> FetchResult:
    """主源失败时用备用源，产物标记 DEGRADED，不得宣称 COMPLETE。"""
    if primary.source_status in {"COMPLETE", "VALID_EMPTY", "NOT_PUBLISHED"}:
        return primary
    if secondary.source_status == "COMPLETE" and secondary.row_count > 0:
        return FetchResult(
            source=secondary.source,
            dataset=secondary.dataset,
            partition_key=secondary.partition_key,
            source_status="DEGRADED",
            rows=secondary.rows,
            row_count=secondary.row_count,
            content_sha256=secondary.content_sha256,
            available_at=secondary.available_at,
            error_reason=f"PRIMARY_{primary.source_status}:{primary.error_reason}",
            summary=dict(secondary.summary) | {"degraded_from": primary.source},
        )
    return primary
