"""The original acquisition thesis, kept apart from a later operations NOI.

One frozen thesis: purchase price, year-2 unlevered NOI, capex, the year-2
unlevered yield on cost from the single yield owner, and ``present_as_bid``.
A later operations actual is a separate NOI. Recording it returns a new
wrapper around the same thesis. This is not an event store, and it does not
rerun the reasonability bands.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from plat_harness.errors import HarnessError, IMPLICIT_ZERO_FORBIDDEN, NOT_FOUND
from plat_harness.underwriting_direction import year_2_unlevered_yield_on_cost


@dataclass(frozen=True)
class OriginalThesis:
    """Figures as first recorded. No field here is a later operations actual."""

    deal_id: str
    purchase_price: Decimal
    year_2_unlevered_noi: Decimal
    capex: Decimal
    year_2_unlevered_yield_on_cost: Decimal
    present_as_bid: bool


@dataclass(frozen=True)
class ThesisRecord:
    """Original thesis plus an optional later operations NOI."""

    thesis: OriginalThesis
    operations_actual_noi: Decimal | None = None


def record_original_thesis(
    deal_id: str,
    *,
    purchase_price: object,
    year_2_unlevered_noi: object,
    capex: object,
    present_as_bid: bool,
) -> ThesisRecord:
    """Store the original thesis. The yield is computed, not supplied."""
    if not isinstance(deal_id, str) or not deal_id.strip():
        raise HarnessError(NOT_FOUND, "An original thesis needs a deal id.")
    if type(present_as_bid) is not bool:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "present_as_bid must be true or false. It is not inferred from the yield.",
        )
    ratio = year_2_unlevered_yield_on_cost(year_2_unlevered_noi, purchase_price, capex)
    thesis = OriginalThesis(
        deal_id=deal_id,
        purchase_price=_amount("purchase_price", purchase_price),
        year_2_unlevered_noi=_amount("year_2_unlevered_noi", year_2_unlevered_noi),
        capex=_amount("capex", capex),
        year_2_unlevered_yield_on_cost=ratio,
        present_as_bid=present_as_bid,
    )
    return ThesisRecord(thesis=thesis)


def record_operations_actual(record: ThesisRecord, noi: object) -> ThesisRecord:
    """Record a later NOI beside the original thesis. The thesis object is unchanged."""
    if not isinstance(record, ThesisRecord):
        raise HarnessError(NOT_FOUND, "No original thesis is stored.")
    return ThesisRecord(
        thesis=record.thesis,
        operations_actual_noi=_amount("operations_actual_noi", noi),
    )


def _amount(name: str, value: object) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            f"{name} is missing; refusing to treat it as zero.",
            details={"field": name},
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
