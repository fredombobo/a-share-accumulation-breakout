"""Bounded statement acquisition through the existing Tushare gateway only.

No writes to SQLite; caller archives immutable observations. Current downloads
are not certified historical vintages, regardless of the vendor's update_flag.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ab_screener.research.fundamental_factors import (
    TZ,
    FactorDataError,
    FinancialObservation,
    aware,
    number,
    period_date,
    publication_floor,
)

META = ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type", "update_flag")
FIELDS = {
    "income": ("revenue", "oper_cost", "basic_eps"),
    "balancesheet": ("total_assets", "total_cur_assets", "money_cap", "total_cur_liab",
                     "st_borr", "non_cur_liab_due_1y", "taxes_payable"),
    "cashflow": ("depr_fa_coga_dpba", "amort_intang_assets"),
}
REQUESTS = (("income", "1"), ("income", "2"), ("balancesheet", "1"), ("cashflow", "1"))
SOURCE = "configured_tushare_https_statements_observed_v1"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str, allow_nan=False).encode()).hexdigest()


def normalize_observation(dataset: str, raw: dict[str, Any], *, ingested_at: str) -> FinancialObservation:
    if dataset not in FIELDS:
        raise FactorDataError("UNKNOWN_DATASET", "不支持的财报接口")
    metadata = {key: "" if raw.get(key) is None else str(raw[key]) for key in META}
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", metadata["ts_code"]):
        raise FactorDataError("INVALID_CODE", "财报代码无效")
    period = period_date(metadata["end_date"])
    floor = publication_floor(metadata["ann_date"], metadata["f_ann_date"])
    if period > min(period_date(metadata["ann_date"]), period_date(metadata["f_ann_date"])):
        raise FactorDataError("INVALID_PUBLICATION_DATE", "财报公告早于报告期")
    values = {key: number(raw.get(key)) for key in FIELDS[dataset]}
    # Content identity retains report type, dates and update flag; no numeric revision guessing.
    revision = canonical_hash({"dataset": dataset, **metadata, "values": values})
    return FinancialObservation(
        dataset=dataset, ts_code=metadata["ts_code"], end_date=metadata["end_date"],
        ann_date=metadata["ann_date"], f_ann_date=metadata["f_ann_date"],
        report_type=metadata["report_type"], comp_type=metadata["comp_type"],
        update_flag=metadata["update_flag"], effective_at=period.isoformat(),
        available_at=max(floor, aware(ingested_at)).isoformat(), ingested_at=ingested_at,
        source=SOURCE, revision=revision, values=values,
    )


def fetch_statement_probe(
    codes: Sequence[str], *, start: str, end: str, provider: Any = None,
    clock: Callable[[], str] | None = None, pause: Callable[[float], None] = time.sleep,
    page_size: int = 200, max_pages: int = 4,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """At most ten explicit codes, four statement requests each, bounded pages.

    Provider failures/ignored pagination do not yield a successful dataset.
    The default path uses the one approved initialization file; injection is for
    deterministic offline tests only.
    """
    if not codes or len(codes) > 10 or len(set(codes)) != len(codes):
        raise ValueError("体检仅接受 1–10 个不重复的明确股票代码，不接受全市场扫描")
    if any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) for code in codes):
        raise ValueError("股票代码必须是标准 TS 代码")
    if period_date(start) > period_date(end):
        raise ValueError("开始日期晚于结束日期")
    if not 1 <= page_size <= 200 or not 1 <= max_pages <= 4:
        raise ValueError("分页超过体检请求预算")
    if provider is None:
        from tushare_init import pro
        provider = pro
    if clock is None:
        clock = lambda: datetime.now(TZ).isoformat()
    observations: dict[str, FinancialObservation] = {}
    calls: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for code in codes:
        for dataset, report_type in REQUESTS:
            if progress:
                progress(f"检查 {code} {dataset} 报告类型 {report_type}")
            seen_pages: set[str] = set()
            dataset_rows: dict[str, FinancialObservation] = {}
            completed = False
            issue: dict[str, str] | None = None
            for page in range(max_pages):
                pause(1.0)
                try:
                    frame = provider.query(dataset, ts_code=code, start_date=start, end_date=end,
                                           report_type=report_type, limit=page_size, offset=page * page_size,
                                           fields=",".join((*META, *FIELDS[dataset])))
                    ingested = clock()  # After request completion, not request start.
                    if frame is None or not hasattr(frame, "to_dict"):
                        raise FactorDataError("INVALID_RESPONSE", "接口未返回表格")
                    raw_rows = frame.to_dict("records")
                    calls.append({"dataset": dataset, "report_type": report_type, "ts_code": code,
                                  "offset": page * page_size, "rows": len(raw_rows), "completed_at": ingested})
                    if not raw_rows:
                        if page == 0:
                            raise FactorDataError("EMPTY_DATASET", "接口返回空数据")
                        completed = True
                        break
                    if len(raw_rows) > page_size:
                        raise FactorDataError("PAGINATION_NOT_HONORED", "供应商忽略分页上限")
                    missing = {*META, *FIELDS[dataset]} - set(frame.columns)
                    if missing:
                        raise FactorDataError("MISSING_COLUMNS", "接口缺字段: " + ",".join(sorted(missing)))
                    page_rows = [normalize_observation(dataset, r, ingested_at=ingested) for r in raw_rows]
                    for row in page_rows:
                        if row.ts_code != code or row.report_type != report_type or not start <= row.ann_date <= end:
                            raise FactorDataError("REQUEST_NOT_HONORED", "接口返回股票/报告类型/公告范围不符合请求")
                    page_hash = canonical_hash(sorted(r.revision for r in page_rows))
                    if page_hash in seen_pages:
                        raise FactorDataError("REPEATED_PAGE", "供应商重复返回同一页，拒绝伪造完整覆盖")
                    seen_pages.add(page_hash)
                    for row in page_rows:
                        dataset_rows.setdefault(row.revision, row)
                    if len(raw_rows) < page_size:
                        completed = True
                        break
                except Exception as exc:  # noqa: BLE001 -- SDK boundary; fail closed and never log credentials.
                    issue = {"dataset": dataset, "report_type": report_type, "ts_code": code,
                             "code": getattr(exc, "code", "PROVIDER_REQUEST_FAILED"),
                             "message": str(exc) if isinstance(exc, FactorDataError)
                             else f"供应商请求失败 ({type(exc).__name__})；检查权限、连接与接口支持"}
                    break
            if not completed:
                issues.append(issue or {"dataset": dataset, "report_type": report_type, "ts_code": code,
                                        "code": "PAGE_BUDGET_EXHAUSTED", "message": "分页预算用尽，不能确认完整性"})
                # A partial request is retained for diagnostics but never used by formulas.
            else:
                observations.update(dataset_rows)
    rows = [asdict(row) for row in sorted(observations.values(), key=lambda r: (r.ts_code, r.dataset, r.end_date, r.revision))]
    return {"source": SOURCE, "start": start, "end": end, "codes": list(codes),
            "status": "COMPLETE" if not issues else "INCOMPLETE", "observations": rows,
            "calls": calls, "issues": issues, "historical_vintages_verified": False,
            "available_at_policy": "max(actual_ingestion, conservative_publication_next_day)",
            "observations_sha256": canonical_hash(rows)}
