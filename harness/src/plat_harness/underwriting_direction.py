"""Directed price and physical-position rules.

One owner for year-2 unlevered yield on cost. Cash-on-cash stays a reported
metric and is not this price. Missing capex is not zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from plat_harness.errors import HarnessError, IMPLICIT_ZERO_FORBIDDEN
from plat_harness.occupancy import require_occupancy_counts

LEASE_UP_BELOW = Decimal("0.85")
YIELD_BAND_LOW = Decimal("0.07")
YIELD_BAND_HIGH = Decimal("0.08")
YIELD_FORMULA = "year_2_unlevered_noi / (purchase_price + capex)"
YIELD_OWNER = "plat_harness.underwriting_direction.year_2_unlevered_yield_on_cost"


@dataclass(frozen=True)
class PhysicalPosition:
    """Lease-up, distressed, or stabilized. Not a rent-gap value-add label."""

    label: str
    rate: Decimal
    occupied: int
    vacant: int
    down: int
    denominator: int

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "rate": format(self.rate, "f"),
            "occupied": self.occupied,
            "vacant": self.vacant,
            "down": self.down,
            "denominator": self.denominator,
        }


def classify_physical_position(
    occupied: object = None,
    vacant: object = None,
    down: object = None,
    denominator: object = None,
    *,
    noi: object = None,
    large_deferred_maintenance: bool = False,
) -> PhysicalPosition | None:
    """Classify only when occupied, vacant, down, and the denominator are present.

    Physical occupancy under 85% is lease-up. Down units, negative NOI, or an
    explicit large deferred-maintenance bill is distressed. Down stays in the
    denominator the caller used. This does not estimate the bill.
    """
    if any(value is None or value == "" for value in (occupied, vacant, down, denominator)):
        return None
    counts = require_occupancy_counts(occupied, vacant, down, denominator)
    rate = Decimal(counts.occupied) / Decimal(counts.denominator)
    distressed = counts.down > 0 or large_deferred_maintenance
    if noi is not None and noi != "":
        distressed = distressed or _require_decimal("noi", noi) < 0
    if distressed:
        label = "distressed"
    elif rate < LEASE_UP_BELOW:
        label = "lease_up"
    else:
        label = "stabilized"
    return PhysicalPosition(
        label=label,
        rate=rate,
        occupied=counts.occupied,
        vacant=counts.vacant,
        down=counts.down,
        denominator=counts.denominator,
    )


def year_2_unlevered_yield_on_cost(
    year_2_unlevered_noi: object,
    purchase_price: object,
    capex: object,
) -> Decimal:
    """Year-2 unlevered NOI / (purchase price + capex). The only owner of that ratio."""
    year_2_unlevered_noi = _require_decimal("year_2_unlevered_noi", year_2_unlevered_noi)
    purchase_price = _require_decimal("purchase_price", purchase_price)
    capex = _require_decimal("capex", capex)
    basis = purchase_price + capex
    if basis <= 0:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "Year-2 unlevered yield on cost needs a positive purchase price plus capex. "
            "Missing capex is not zero, and a missing price is not a zero yield.",
            details={"formula": YIELD_FORMULA},
        )
    return year_2_unlevered_noi / (purchase_price + capex)


def yield_in_band(ratio: Decimal) -> bool:
    """True when the ratio sits in the closed 7–8% band."""
    return YIELD_BAND_LOW <= ratio <= YIELD_BAND_HIGH


def _require_decimal(name: str, value: object) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            f"{name} is missing; refusing to treat it as zero.",
            details={"field": name, "formula": YIELD_FORMULA},
        )
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            f"{name} must be a decimal amount, not {type(value).__name__}.",
            details={"field": name},
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            f"{name} is not a decimal amount.",
            details={"field": name},
        ) from exc
    if not parsed.is_finite():
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            f"{name} is not a finite decimal amount.",
            details={"field": name},
        )
    return parsed
