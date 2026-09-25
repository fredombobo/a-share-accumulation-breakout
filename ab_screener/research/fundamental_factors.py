"""Independently implemented accounting-factor definitions; no data-provider SDK.

These are research signals, not trading instructions or claims of A-share alpha.
New observations cannot be backdated to their accounting or announcement date.
"""
from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
VERSION = "fundamental-factors-v1.0.0"
UPSTREAM_COMMIT = "8db892442c2c3a3779b0f1eac4370d3655be15a1"
UPSTREAM_FILE_HASHES = {
    "GP": "6a05de4a5b6ddb47a320e1d95d6392e625bfca3b50091e698be9fd866a6c8576",
    "Accruals": "847b1889c54b1c913f94a63647611a98ae908cc33e6364fa7ac2adf5b62d916c",
    "EarningsSurprise": "bc5ddeb08dbff2036e5443f06b895c113a8d556ed577c9a8a8d7e23b7e52c279",
}
GP = "gp_assets_annual_v1"
ACCRUALS = "low_accruals_annual_v1"
SUE = "earnings_surprise_quarterly_v1"
FACTOR_IDS = (GP, ACCRUALS, SUE)


class FactorDataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise FactorDataError("TIMEZONE_REQUIRED", "财务数据时间必须带时区")
    return parsed.astimezone(TZ)


def period_date(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y%m%d").replace(tzinfo=TZ)
    if parsed.strftime("%Y%m%d") != value:
        raise FactorDataError("INVALID_DATE", "日期必须为 YYYYMMDD")
    return parsed


def publication_floor(ann_date: str, f_ann_date: str) -> datetime:
    return max(period_date(ann_date), period_date(f_ann_date)) + timedelta(days=1)


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


@dataclass(frozen=True)
class FinancialObservation:
    dataset: str
    ts_code: str
    end_date: str
    ann_date: str
    f_ann_date: str
    report_type: str
    comp_type: str
    update_flag: str
    effective_at: str
    available_at: str
    ingested_at: str
    source: str
    revision: str
    values: Mapping[str, Decimal | None]
    # Same verified per-share basis across all required quarters, not just a bool.
    eps_basis_evidence: str | None = None


@dataclass(frozen=True)
class FactorResult:
    factor_id: str
    status: str
    value: str | None
    code: str
    message: str
    decision_at: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    candidate_eligible: bool = False


def factor_catalog() -> list[dict[str, Any]]:
    definitions = [
        (GP, "毛盈利能力 / 总资产", "GP", "Novy-Marx (2013)",
         "(revenue-oper_cost)/total_assets", ["income", "balancesheet"]),
        (ACCRUALS, "低应计利润", "Accruals", "Sloan (1996), A-share adaptation",
         "-[(delta_CA-delta_cash)-(delta_CL-delta_short_debt-delta_tax)-DA]/avg_assets",
         ["balancesheet", "cashflow"]),
        (SUE, "标准化盈利意外", "EarningsSurprise", "Foster/Olsen/Shevlin (1984)",
         "u(q)/sample_std(u(q-1)..u(q-8)); u=g-mean(g(q-1)..g(q-8)); g=EPSq-EPSq4",
         ["income", "verified_eps_basis"]),
    ]
    return [{"id": ident, "title": title, "version": VERSION, "paper": paper,
             "formula": formula, "datasets": datasets, "larger_is_better": True,
             "source_url": f"https://github.com/OpenSourceAP/CrossSection/blob/{UPSTREAM_COMMIT}"
                           f"/Signals/pyCode/Predictors/{filename}.py",
             "upstream_license": "GPL-2.0", "implementation": "independent_formula_adaptation",
             "upstream_file_sha256": UPSTREAM_FILE_HASHES[filename],
             "production_ready": False, "candidate_eligible": False}
            for ident, title, filename, paper, formula, datasets in definitions]


def _visible(rows: Sequence[FinancialObservation], code: str, at: datetime) -> list[FinancialObservation]:
    visible = []
    for row in rows:
        if row.ts_code != code or aware(row.available_at) > at:
            continue
        floor = publication_floor(row.ann_date, row.f_ann_date)
        if not row.source or not row.revision or aware(row.available_at) < max(floor, aware(row.ingested_at)):
            raise FactorDataError("INVALID_LINEAGE", "缺少来源/版本或可用时间早于实际观察/公告")
        if aware(row.effective_at) != period_date(row.end_date):
            raise FactorDataError("INVALID_LINEAGE", "经济时点与报告期不一致")
        if period_date(row.end_date) > min(period_date(row.ann_date), period_date(row.f_ann_date)):
            raise FactorDataError("INVALID_PUBLICATION_DATE", "公告日期早于报告期末")
        visible.append(row)
    if not visible:
        raise FactorDataError("NO_PIT_OBSERVATIONS", "决策时点没有可用财务记录；不能使用以后抓取的数据")
    return visible


def _select(rows: Sequence[FinancialObservation], dataset: str, period: str, report_type: str) -> FinancialObservation:
    matches = [r for r in rows if r.dataset == dataset and r.end_date == period and r.report_type == report_type]
    if not matches:
        raise FactorDataError("MISSING_REPORT", f"缺少 {dataset} / {period} / 报告类型 {report_type}")
    # A batch observed today can contain several vendor publication vintages.
    # Distinct dated publications are ordered; conflicting same-date vintages
    # are rejected, never resolved by row order or update_flag alone.
    def order(row: FinancialObservation) -> tuple[datetime, datetime]:
        return publication_floor(row.ann_date, row.f_ann_date), aware(row.available_at)

    latest = max(order(r) for r in matches)
    selected = [r for r in matches if order(r) == latest]
    if len({r.revision for r in selected}) > 1:
        raise FactorDataError("AMBIGUOUS_REVISION", f"{dataset}/{period} 同时点存在冲突版本")
    row = selected[0]
    if row.comp_type != "1":
        raise FactorDataError("NON_INDUSTRIAL_COMPANY", "此因子适配只支持一般工商业，金融企业或未知类型排除")
    return row


def _value(row: FinancialObservation, key: str) -> Decimal:
    value = number(row.values.get(key))
    if value is None:
        raise FactorDataError("MISSING_FIELD", f"{row.dataset}/{row.end_date} 缺少有效 {key}；不填零")
    return value


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise FactorDataError("INVALID_ASSETS", "资产分母必须为正")
    return value


def _latest_period(rows: Sequence[FinancialObservation], dataset: str, report_type: str, annual: bool) -> str:
    periods = [r.end_date for r in rows if r.dataset == dataset and r.report_type == report_type
               and (r.end_date.endswith("1231") if annual else r.end_date[4:] in ("0331", "0630", "0930", "1231"))]
    if not periods:
        raise FactorDataError("MISSING_REPORT", "缺少所需年报或明确单季合并报表")
    return max(periods)


def _quarter(period: str, lag: int) -> str:
    end = period_date(period)
    index = end.year * 4 + (end.month - 1) // 3 - lag
    year, quarter = divmod(index, 4)
    month = (quarter + 1) * 3
    return f"{year:04}{month:02}{calendar.monthrange(year, month)[1]:02}"


def _calculate(rows: Sequence[FinancialObservation], factor: str, at: datetime) -> tuple[Decimal, list[FinancialObservation]]:
    if factor == SUE:
        period = _latest_period(rows, "income", "2", False)
        if (at - period_date(period)).days > 200:
            raise FactorDataError("STALE_REPORT", "最新单季报告超过 200 日")
        quarters = [_select(rows, "income", _quarter(period, lag), "2") for lag in range(21)]
        eps = [_value(r, "basic_eps") for r in quarters]
        basis = {r.eps_basis_evidence for r in quarters}
        if None in basis or "" in basis or len(basis) != 1:
            raise FactorDataError("EPS_BASIS_UNVERIFIED", "缺少连续 21 季每股口径与拆并股可比性证据")
        growth = [eps[i] - eps[i + 4] for i in range(17)]
        surprise = [growth[i] - sum(growth[i + 1:i + 9], Decimal(0)) / 8 for i in range(9)]
        mean = sum(surprise[1:], Decimal(0)) / 8
        sd = (sum(((v - mean) ** 2 for v in surprise[1:]), Decimal(0)) / 7).sqrt()
        if sd <= Decimal("1e-10"):
            raise FactorDataError("ZERO_VARIANCE", "历史盈利意外波动为零或过小，不能标准化")
        return surprise[0] / sd, quarters
    period = _latest_period(rows, "income" if factor == GP else "balancesheet", "1", True)
    if (at - period_date(period)).days > 550:
        raise FactorDataError("STALE_REPORT", "最新年报超过 550 日")
    balance = _select(rows, "balancesheet", period, "1")
    assets = _positive(_value(balance, "total_assets"))
    if factor == GP:
        income = _select(rows, "income", period, "1")
        return (_value(income, "revenue") - _value(income, "oper_cost")) / assets, [income, balance]
    prior = _select(rows, "balancesheet", f"{int(period[:4]) - 1}1231", "1")
    flow = _select(rows, "cashflow", period, "1")
    average = (assets + _positive(_value(prior, "total_assets"))) / 2

    def delta(key: str) -> Decimal:
        return _value(balance, key) - _value(prior, key)

    working = ((delta("total_cur_assets") - delta("money_cap"))
               - (delta("total_cur_liab") - delta("st_borr") - delta("non_cur_liab_due_1y")
                  - delta("taxes_payable")))
    depreciation = _value(flow, "depr_fa_coga_dpba") + _value(flow, "amort_intang_assets")
    if depreciation < 0:
        raise FactorDataError("INVALID_DEPRECIATION", "折旧摊销不能为负")
    return -(working - depreciation) / average, [balance, prior, flow]


def evaluate_factor(rows: Sequence[FinancialObservation], *, ts_code: str, factor_id: str,
                    decision_at: str) -> FactorResult:
    """Evaluate known observations only; failures contain no invented score."""
    try:
        if factor_id not in FACTOR_IDS:
            raise FactorDataError("UNKNOWN_FACTOR", "未知财务因子")
        at = aware(decision_at)
        with localcontext() as ctx:
            ctx.prec = 34
            result, used = _calculate(_visible(rows, ts_code, at), factor_id, at)
        return FactorResult(factor_id, "COMPUTABLE", str(result), "OK",
                            "仅表示公式可计算，不表示已验证盈利或历史面板合格", decision_at,
                            tuple(sorted({r.revision for r in used})))
    except (ValueError, InvalidOperation) as exc:
        return FactorResult(factor_id, "BLOCKED", None,
                            getattr(exc, "code", "INVALID_INPUT"), str(exc), decision_at)
