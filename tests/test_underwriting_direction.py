"""Occupancy position and the single year-2 unlevered yield owner."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from plat_harness.errors import IMPLICIT_ZERO_FORBIDDEN, HarnessError
from plat_harness.underwriting_direction import (
    LEASE_UP_BELOW,
    YIELD_BAND_HIGH,
    YIELD_BAND_LOW,
    YIELD_FORMULA,
    YIELD_OWNER,
    classify_physical_position,
    year_2_unlevered_yield_on_cost,
    yield_in_band,
)


def test_occupancy_under_85_percent_is_lease_up() -> None:
    position = classify_physical_position(67, 13, 0, 80)
    assert position is not None
    assert position.label == "lease_up"
    assert position.rate < LEASE_UP_BELOW
    assert position.down == 0


def test_occupancy_at_85_percent_without_distress_is_stabilized() -> None:
    position = classify_physical_position(68, 12, 0, 80, noi=Decimal("100"))
    assert position is not None
    assert position.rate == Decimal("0.85")
    assert position.label == "stabilized"


def test_down_units_negative_noi_or_large_deferred_maintenance_is_distressed() -> None:
    down = classify_physical_position(90, 8, 2, 100)
    assert down is not None and down.label == "distressed"

    negative = classify_physical_position(95, 5, 0, 100, noi=Decimal("-1"))
    assert negative is not None and negative.label == "distressed"

    bill = classify_physical_position(
        95,
        5,
        0,
        100,
        noi=Decimal("100"),
        large_deferred_maintenance=True,
    )
    assert bill is not None and bill.label == "distressed"

    aspen = classify_physical_position(335, 72, 1, 408)
    assert aspen is not None
    assert aspen.label == "distressed"
    assert aspen.rate < LEASE_UP_BELOW


def test_incomplete_counts_do_not_reclassify() -> None:
    assert classify_physical_position(occupied=10, vacant=1, down=0) is None


def test_single_yield_owner(glossary) -> None:
    metric = glossary.require("yield_on_cost")
    assert metric.status == "CERTIFIED"
    assert len(metric.definitions) == 1
    definition = metric.definitions[0]
    assert definition.owner == YIELD_OWNER
    assert definition.formula == YIELD_FORMULA
    owners = [
        item.owner
        for row in glossary
        for item in row.definitions
        if item.formula and "purchase_price + capex" in item.formula
    ]
    assert owners == [YIELD_OWNER]

    cash_on_cash = glossary.require("cash_on_cash")
    assert cash_on_cash.status == "CONFLICT"
    assert all(item.owner != YIELD_OWNER for item in cash_on_cash.definitions)
    assert "not the purchase price" in (cash_on_cash.handshake or "")

    root = Path(__file__).resolve().parents[1]
    skip = {".git", ".venv", "__pycache__", "dist", "build", ".pytest_cache", "tests"}
    hits: list[str] = []
    for path in root.rglob("*"):
        if skip.intersection(path.parts) or not path.is_file():
            continue
        if path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        if "purchase_price + capex" in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(root).as_posix())
    assert sorted(hits) == [
        "docs/glossary.yaml",
        "harness/src/plat_harness/glossary.yaml",
        "harness/src/plat_harness/underwriting_direction.py",
    ]


def test_year_2_yield_is_noi_over_price_plus_capex_in_band() -> None:
    ratio = year_2_unlevered_yield_on_cost(
        Decimal("80000"),
        Decimal("900000"),
        Decimal("100000"),
    )
    assert ratio == Decimal("0.08")
    assert yield_in_band(ratio)
    assert YIELD_BAND_LOW == Decimal("0.07")
    assert YIELD_BAND_HIGH == Decimal("0.08")

    levered_cash_on_cash = (Decimal("80000") - Decimal("50000")) / Decimal("350000")
    assert ratio != levered_cash_on_cash


def test_missing_capex_is_not_zero() -> None:
    with pytest.raises(HarnessError) as caught:
        year_2_unlevered_yield_on_cost(Decimal("80000"), Decimal("1000000"), None)
    assert caught.value.code == IMPLICIT_ZERO_FORBIDDEN
    assert "zero" in caught.value.message
