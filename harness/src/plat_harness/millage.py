"""Millage gate. Copied prompt text from plat-agent; do not import plat_agent."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from plat_harness.errors import HarnessError, MISSING_MILLAGE

# Same operator wording as plat_agent.lifecycle.property_tax.PROPERTY_TAX_MILLAGE_QUESTION.
PROPERTY_TAX_MILLAGE_QUESTION = (
    "What combined property-tax millage should be used? Enter mills per $1,000 "
    "of assessed value (for example, `25.31`)."
)

SOURCE_LOCATOR = "plat-harness:property_tax_millage"


def parse_millage_rate(value: object) -> Decimal:
    """Return a positive finite millage, or raise MISSING_MILLAGE / invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HarnessError(
            MISSING_MILLAGE,
            PROPERTY_TAX_MILLAGE_QUESTION,
        )
    if isinstance(value, bool):
        raise HarnessError(
            MISSING_MILLAGE,
            "The submitted property-tax millage is not a positive number of mills per $1,000.",
            details={"submitted_value": value},
        )
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise HarnessError(
            MISSING_MILLAGE,
            "The submitted property-tax millage is not a positive number of mills per $1,000.",
            details={"submitted_value": value},
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        raise HarnessError(
            MISSING_MILLAGE,
            "The submitted property-tax millage is not a positive number of mills per $1,000.",
            details={"submitted_value": str(value)},
        )
    return parsed
