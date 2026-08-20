"""dual-run：v2 执行核心与旧核心对比（P2.1 验收：不一致不切换写路径）。

旧核心 = ab_screener.domain.costs（float 元口径）；v2 = execution（整数分）。
`compare_round_trip` 把同一 K 线场景分别交给两个核心，报告逐项差异；
`parity` 为 True 时（差异在容差内）才允许切换写路径。
"""
from __future__ import annotations

from typing import Any


def compare_round_trip(
    v2_result: dict[str, Any],
    legacy_result: dict[str, Any],
    *,
    price_tolerance_micro: int = 1,
) -> dict[str, Any]:
    """对比单笔往返的 qty/price/费用；返回 {parity, diffs}。

    v2_result: FillV2.to_dict()（买/卖两笔）；legacy: costs.FillResult.to_dict()。
    差异容差：价格 ±1 微元（浮点转整数舍入）；金额按分取整后 ±1 分。
    """
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
