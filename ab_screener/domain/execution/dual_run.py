"""dual-run：v2 执行核心与旧核心对比（P2.1 验收：不一致不切换写路径）。

旧核心 = paper_trading.engine.estimate_fill（浮点元口径 + 手单位 vol）；
v2 = ab_screener.domain.execution（整数分 + 股单位 vol）。
`compare_round_trip` / `dual_run_compare` 把同一冻结行情/规则/订单分别交给两个核心，
报告逐项差异（成交数量/价格/佣金/税费/其他费用/现金变化/持仓变化）；
`parity` 为 True 时（差异为零）才允许切换写路径。V2_EXECUTION_WRITE_ENABLED 默认
false：写路径始终走 legacy，dual-run 只记录比较证据，绝不写第二笔成交/现金/持仓。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from ab_screener.domain.execution.fees import FeeParams, compute_fees
from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import FillV2, Quote

DEFAULT_PARTICIPATION_BPS = 500


@dataclass(frozen=True)
class FrozenOrder:
    """冻结场景：同一 K 线 + 规则 + 订单，交给 legacy 与 v2 核心对比。

    bar 的字段与 paper_trading.engine._day_bar 一致：open/high/low/close（元 float）、
    vol（手）、amount（元）、pre_close（元，可缺省）；bar=None 表示停牌/无行情。
    """

    bar: dict[str, Any] | None
    side: str
    qty: int
    rule: Any  # paper_trading.rules.InstrumentRule
    ts_code: str
    trade_date: str
    input_hash: str
    cash_available_fen: int | None = None
    position_qty: int | None = None
    participation_bps: int = DEFAULT_PARTICIPATION_BPS


def _yuan_to_micro(value: Any) -> int:
    return int(round(float(value) * 1_000_000))


def _yuan_to_fen(value: Any) -> int:
    return int(round(float(value) * 100))


def _lts_to_shares(value: Any) -> int:
    """daily.vol 单位是「手」（100 股/手）→ 转股。"""
    return int(float(value) * 100)


def _fee_params(rule: Any) -> FeeParams:
    return FeeParams(
        commission_bps=int(rule.commission_bps),
        commission_min_fen=int(rule.min_commission_fen),
        stamp_tax_bps=int(rule.sell_tax_bps),
        other_fee_bps=int(rule.other_fee_bps),
        slippage_bps=int(rule.slippage_bps),
    )


def _compute_v2(frozen: FrozenOrder) -> FillV2:
    """把冻结场景喂给 v2 执行核心。bar=None → NO_QUOTE 零成交。"""
    side = cast(Literal["BUY", "SELL"], frozen.side)
    if frozen.bar is None:
        return FillV2(
            ts_code=frozen.ts_code, side=side, trade_date=frozen.trade_date,
            filled=False, qty=0, price_micro=0, notional_fen=0,
            fees=compute_fees(0, side, _fee_params(frozen.rule),
                              slippage_notional_fen=0),
            cash_delta_fen=0, reason="NO_QUOTE",
            participation_bps=frozen.participation_bps, max_qty=0,
            input_hash=frozen.input_hash,
        )
    bar = frozen.bar
    quote = Quote(
        ts_code=frozen.ts_code, trade_date=frozen.trade_date,
        open_micro=_yuan_to_micro(bar.get("open")),
        high_micro=_yuan_to_micro(bar.get("high")),
        low_micro=_yuan_to_micro(bar.get("low")),
        close_micro=_yuan_to_micro(bar.get("close")),
        vol=_lts_to_shares(bar.get("vol")),
        amount_fen=_yuan_to_fen(bar.get("amount", 0)),
        pre_close_micro=_yuan_to_micro(bar["pre_close"]) if bar.get("pre_close") else None,
        available_at=bar.get("available_at", ""),
    )
    return compute_fill(
        quote,
        FillRequest(
            ts_code=frozen.ts_code, side=side, trade_date=frozen.trade_date,
            input_hash=frozen.input_hash,
            requested_qty=frozen.qty,
            participation_bps=frozen.participation_bps,
            lot_size=int(frozen.rule.lot_size),
            cash_available_fen=frozen.cash_available_fen,
            position_qty=frozen.position_qty,
            fees=_fee_params(frozen.rule),
        ),
    )


def _legacy_cash_delta(side: str, legacy: dict[str, Any]) -> int:
    notional = int(legacy.get("notional_fen", 0))
    commission = int(legacy.get("commission_fen", 0))
    tax = int(legacy.get("tax_fen", 0))
    other = int(legacy.get("other_fee_fen", 0))
    if side == "BUY":
        return -(notional + commission + tax + other)
    return notional - commission - tax - other


def _build_comparison(
    frozen: FrozenOrder, legacy: dict[str, Any], v2: FillV2,
) -> dict[str, Any]:
    legacy_qty = int(legacy.get("fill_qty", 0))
    legacy_filled = legacy_qty > 0
    v2_qty = int(v2.qty)
    v2_filled = bool(v2.filled)

    quantity_diff = v2_qty - legacy_qty
    position_diff_qty = quantity_diff
    cash_diff_fen = int(v2.cash_delta_fen) - _legacy_cash_delta(frozen.side, legacy)

    if legacy_filled and v2_filled:
        price_diff_micro = int(v2.price_micro) - int(legacy.get("fill_price_micro", 0))
        commission_diff_fen = int(v2.fees.commission_fen) - int(legacy.get("commission_fen", 0))
        tax_diff_fen = int(v2.fees.stamp_tax_fen) - int(legacy.get("tax_fen", 0))
        other_fee_diff_fen = int(v2.fees.other_fee_fen) - int(legacy.get("other_fee_fen", 0))
    else:
        # 零成交无经济交易：数量/现金/费用一律按 0 分对比
        price_diff_micro = 0
        commission_diff_fen = 0
        tax_diff_fen = 0
        other_fee_diff_fen = 0

    diffs: list[str] = []
    if quantity_diff != 0:
        diffs.append(f"qty: v2={v2_qty} legacy={legacy_qty}")
    if price_diff_micro != 0:
        diffs.append(f"price: 差 {price_diff_micro} 微元")
    if commission_diff_fen != 0:
        diffs.append(
            f"commission: v2={v2.fees.commission_fen}分 legacy={legacy.get('commission_fen')}分"
        )
    if tax_diff_fen != 0:
        diffs.append(
            f"stamp_tax: v2={v2.fees.stamp_tax_fen}分 legacy={legacy.get('tax_fen')}分"
        )
    if other_fee_diff_fen != 0:
        diffs.append(
            f"other_fee: v2={v2.fees.other_fee_fen}分 legacy={legacy.get('other_fee_fen')}分"
        )
    if cash_diff_fen != 0:
        diffs.append(f"cash: v2={v2.cash_delta_fen}分 legacy={_legacy_cash_delta(frozen.side, legacy)}分")
    if legacy_filled != v2_filled:
        diffs.append(
            f"fill-mismatch: v2_filled={v2_filled} legacy_filled={legacy_filled}"
            f" reason={legacy.get('reason')}"
        )

    fee_diff_fen = abs(commission_diff_fen) + abs(tax_diff_fen) + abs(other_fee_diff_fen)
    return {
        "parity": not diffs,
        "diffs": diffs,
        "quantity_diff": quantity_diff,
        "position_diff_qty": position_diff_qty,
        "price_diff_micro": price_diff_micro,
        "commission_diff_fen": commission_diff_fen,
        "tax_diff_fen": tax_diff_fen,
        "other_fee_diff_fen": other_fee_diff_fen,
        "fee_diff_fen": fee_diff_fen,
        "cash_diff_fen": cash_diff_fen,
        "legacy_reason": str(legacy.get("reason") or ""),
        "v2_reason": v2.reason,
        "legacy": legacy,
        "v2": v2.to_dict(),
    }


def dual_run_compare(frozen: FrozenOrder) -> dict[str, Any]:
    """同一冻结行情/规则/订单分别交给 legacy 与 v2 核心，返回逐项差异。

    延迟导入 paper_trading.engine 避免模块级循环依赖（engine → dual_run）。
    """
    if frozen.bar is None:
        legacy: dict[str, Any] = {"fill_qty": 0, "reason": "NO_QUOTE"}
    else:
        from paper_trading.engine import estimate_fill

        legacy = estimate_fill(frozen.bar, frozen.side, frozen.qty, frozen.rule)
    v2 = _compute_v2(frozen)
    return _build_comparison(frozen, legacy, v2)


def compare_round_trip(
    v2_result: dict[str, Any] | FrozenOrder,
    legacy_result: dict[str, Any] | None = None,
    *,
    price_tolerance_micro: int = 1,
) -> dict[str, Any]:
    """双入口比较器。

    - 传入单个 FrozenOrder：走 `dual_run_compare`（V2R-X 冻结场景入口），
      返回含 quantity_diff/cash_diff_fen/fee_diff_fen 的逐项比较结果。
    - 传入 (v2_result, legacy_result) 字典：保留原 dict 级对比（P2.1 旧契约）。
    """
    if isinstance(v2_result, FrozenOrder):
        return dual_run_compare(v2_result)

    legacy_result = legacy_result or {}
    diffs: list[str] = []
    for field in ("qty",):
        v2 = v2_result.get(field)
        old = legacy_result.get(field)
        if v2 != old:
            diffs.append(f"{field}: v2={v2} legacy={old}")

    def _price_diff(v2_micro: int | None, legacy_price: float | None) -> int | None:
        if v2_micro is None or legacy_price is None:
            return None
        legacy_micro = int(round(float(legacy_price) * 1_000_000))
        return abs(int(v2_micro) - legacy_micro)

    for field, v2_key in (("price", "price_micro"),):
        legacy_px = legacy_result.get(field)
        v2_px = v2_result.get(v2_key)
        if legacy_px is not None and v2_px is not None:
            diff = _price_diff(v2_px, legacy_px)
            if diff is not None and diff > price_tolerance_micro:
                diffs.append(f"{field}: 差 {diff} 微元")

    for field in ("commission", "stamp_tax", "other_fee"):
        v2_fen = v2_result.get("fee_breakdown", {}).get(f"{field}_fen")
        old_float = legacy_result.get(field)
        if v2_fen is not None and old_float is not None:
            old_fen = int(round(float(old_float) * 100))
            if abs(int(v2_fen) - old_fen) > 1:
                diffs.append(f"{field}: v2={v2_fen}分 legacy≈{old_fen}分")

    return {"parity": not diffs, "diffs": diffs}


# ── 配置旗标（默认写路径关闭 / dual-run 观察开启 / 风控 enforce 关闭） ──


def _resolved_flags() -> dict[str, bool]:
    try:
        from ab_screener.application.platform_config import load_resolved_config

        return dict(load_resolved_config().get("flags") or {})
    except Exception:  # noqa: BLE001
        return {}


def dual_run_enabled() -> bool:
    """V2_EXECUTION_DUAL_RUN_ENABLED（默认 true）：只观察比较，不写第二份账。"""
    return bool(_resolved_flags().get("V2_EXECUTION_DUAL_RUN_ENABLED", True))


def write_path_enabled() -> bool:
    """V2_EXECUTION_WRITE_ENABLED（默认 false）：切换 v2 写路径前必须 parity。"""
    return bool(_resolved_flags().get("V2_EXECUTION_WRITE_ENABLED", False))


def risk_enforcement_enabled() -> bool:
    """V2_RISK_ENFORCEMENT_ENABLED（默认 false）：observe → enforce。"""
    return bool(_resolved_flags().get("V2_RISK_ENFORCEMENT_ENABLED", False))
