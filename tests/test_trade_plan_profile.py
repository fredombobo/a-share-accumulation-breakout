from __future__ import annotations

from trade_plan import build_trade_card


def test_trade_card_uses_profile_stop_and_target_percentages() -> None:
    card = build_trade_card(
        price=10.0,
        box_high=None,
        box_low=8.5,
        breakout_date="20260828",
        stop_pct=0.06,
        target_pct=0.16,
    )

    assert card["stop_loss"] == 9.4
    assert card["target_1"] == 11.6
    assert card["target_2"] is None
    assert "+16.0%" in card["target_rule"]


def test_trade_card_clamps_out_of_contract_risk_references() -> None:
    card = build_trade_card(
        price=10.0,
        box_high=None,
        box_low=None,
        breakout_date=None,
        stop_pct=-1,
        target_pct=2,
    )

    assert card["stop_loss"] == 9.9
    assert card["target_1"] == 20.0
