"""TEST-001 engine figures are copied and withheld. Year-1 yield is not year-2 yield."""

from __future__ import annotations

from decimal import Decimal

import pytest

from plat_harness.errors import IMPLICIT_ZERO_FORBIDDEN, HarnessError
from plat_harness.reasonability import present_underwriting
from plat_harness.underwriting_direction import (
    classify_physical_position,
    year_2_unlevered_yield_on_cost,
)

# Copied from engine.engine.run_underwriting(minimal_deal_inputs) for deal TEST-001.
# purchase_price is the fixture input. price_per_unit is that price / 100 units.
# going_in_yield_on_cost 0.0885 is year-1 NOI / total investment and is not passed in.
_TEST_001 = {
    "purchase_price": 13_500_000,
    "going_in_cap_rate": "0.0895",
    "exit_cap_rate": "0.055",
    "price_per_unit": 135_000,
    "units": 100,
    "minimum_dscr": "1.91",
    "year_1_noi": "1207654.8",
}


def test_test001_exit_cap_withholds_and_copies_engine_figures() -> None:
    issued = present_underwriting(_TEST_001, kind="solved_strike")
    engine = issued["engine"]
    assert issued["present_as_bid"] is False
    assert issued["bid"] is None
    assert engine["strike"] == Decimal("13500000")
    assert engine["going_in_cap"] == Decimal("0.0895")
    assert engine["exit_cap"] == Decimal("0.055")
    assert engine["price_per_unit"] == Decimal("135000")
    assert engine["units"] == 100
    assert engine["min_dscr"] == Decimal("1.91")
    assert engine["year1_noi"] == Decimal("1207654.8")
    assert issued["breaches"] == [
        {"field": "exit_cap", "value": "0.055", "band": "[0.06, 0.12]"}
    ]
    assert "0.0885" not in {format(value, "f") for value in engine.values() if isinstance(value, Decimal)}
    assert not any("year_2" in key or "year2" in key for key in engine)


def test_test001_does_not_invent_year2_yield_or_occupancy() -> None:
    assert classify_physical_position() is None
    with pytest.raises(HarnessError) as caught:
        year_2_unlevered_yield_on_cost(None, Decimal("13500000"), None)
    assert caught.value.code == IMPLICIT_ZERO_FORBIDDEN
    assert "year_2_unlevered_noi" in caught.value.message
