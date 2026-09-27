"""The TEST-001 original thesis stays put when a later operations NOI is recorded."""

from __future__ import annotations

from decimal import Decimal

from plat_harness.original_thesis import record_operations_actual, record_original_thesis
from plat_harness.underwriting_direction import year_2_unlevered_yield_on_cost

_PRICE = Decimal("13500000")
_YEAR_2_NOI = Decimal("1200004.80")
_CAPEX = Decimal("1758150")
_LATER_NOI = Decimal("640000")


def test_test001_thesis_survives_a_later_operations_actual() -> None:
    stored = record_original_thesis(
        "TEST-001",
        purchase_price=_PRICE,
        year_2_unlevered_noi=_YEAR_2_NOI,
        capex=_CAPEX,
        present_as_bid=False,
    )
    thesis = stored.thesis
    assert thesis.deal_id == "TEST-001"
    assert thesis.purchase_price == _PRICE
    assert thesis.year_2_unlevered_noi == _YEAR_2_NOI
    assert thesis.capex == _CAPEX
    assert thesis.year_2_unlevered_yield_on_cost == year_2_unlevered_yield_on_cost(
        _YEAR_2_NOI, _PRICE, _CAPEX
    )
    assert abs(thesis.year_2_unlevered_yield_on_cost - Decimal("0.07865")) < Decimal("0.000005")
    assert thesis.present_as_bid is False
    assert stored.operations_actual_noi is None

    updated = record_operations_actual(stored, _LATER_NOI)
    assert updated.thesis is thesis
    assert updated.thesis.purchase_price == _PRICE
    assert updated.thesis.year_2_unlevered_noi == _YEAR_2_NOI
    assert updated.thesis.capex == _CAPEX
    assert updated.thesis.year_2_unlevered_yield_on_cost == thesis.year_2_unlevered_yield_on_cost
    assert updated.thesis.present_as_bid is False
    assert updated.operations_actual_noi == _LATER_NOI
    assert updated.operations_actual_noi != updated.thesis.year_2_unlevered_noi
    assert stored.thesis == thesis
    assert stored.operations_actual_noi is None
