"""Withhold a bid when an issued engine result is outside presentation bands.

The engine's NOI and strike are copied through. This module does not recompute
them. A model that calls the strike reasonable, and a permission rank, cannot
restore the bid.

No as-issued history is stored. This module does not invent an event store.
"""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
from typing import Any

from plat_harness.errors import HarnessError, IMPLICIT_ZERO_FORBIDDEN, NOT_FOUND
from plat_harness.ranks import PermissionRank

CAP_LOW = Decimal("0.06")
CAP_HIGH = Decimal("0.12")
PRICE_PER_UNIT_LOW = Decimal("25000")
PRICE_PER_UNIT_HIGH = Decimal("750000")
DSCR_FLOOR = Decimal("1.10")

PRESENTATION_KINDS = frozenset({"solved_strike", "triangle", "scoreboard"})

_APPROVE_FLAGS = frozenset({"reasonable", "approve", "pass", "true"})


def present_underwriting(
    result: dict[str, Any],
    *,
    kind: str = "solved_strike",
    market: str | None = None,
    model_reasonable: bool = False,
    session_rank: PermissionRank | None = None,
) -> dict[str, Any]:
    """Copy the engine figures and withhold a bid on a band breach.

    ``model_reasonable`` and ``session_rank`` are recorded. They are not inputs
    to ``present_as_bid``.
    """
    if kind not in PRESENTATION_KINDS:
        raise HarnessError(
            NOT_FOUND,
            f"Unknown presentation kind '{kind}'.",
            details={"kind": kind},
        )
    if not isinstance(result, dict):
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "An issued underwriting result must be an object. Refusing to invent figures.",
        )
    engine = _engine_figures(result)
    breaches = _breaches(engine)
    has_strike = engine["strike"] is not None
    present_as_bid = has_strike and not breaches
    bid = engine["strike"] if present_as_bid else None
    return {
        "kind": kind,
        "market": market,
        "present_as_bid": present_as_bid,
        "bid": bid,
        "engine": engine,
        "breaches": breaches,
        "model_called_reasonable": bool(model_reasonable),
        "session_rank": None if session_rank is None else int(session_rank),
        "as_issued_history": None,
    }


def approve_issued_result(
    issued: dict[str, Any],
    *,
    session_rank: PermissionRank,
    reasonable: bool = False,
    flag: str | None = None,
) -> dict[str, Any]:
    """A rank or a flag cannot approve an issued result into a bid.

    There is no as-issued history on this package. This does not write one.
    """
    if not isinstance(issued, dict) or not isinstance(issued.get("engine"), dict):
        raise HarnessError(
            NOT_FOUND,
            "No issued underwriting result to approve.",
        )
    flagged = bool(reasonable) or (flag is not None and flag.strip().lower() in _APPROVE_FLAGS)
    present_as_bid = bool(issued.get("present_as_bid"))
    engine = copy.deepcopy(issued["engine"])
    return {
        "present_as_bid": present_as_bid,
        "bid": None if not present_as_bid else copy.deepcopy(issued.get("bid")),
        "engine": engine,
        "approved": False,
        "session_rank": int(session_rank),
        "reasonable_flag": flagged,
        "flag": flag,
        "as_issued_history": None,
    }


def _engine_figures(result: dict[str, Any]) -> dict[str, Any]:
    """Echo parsed inputs. Do not derive NOI from cap or price."""
    units = _optional_int(_lookup(result, ("units",)))
    strike = _optional_decimal(_lookup(result, ("strike", "solved_purchase_price", "purchase_price")))
    going_in_cap = _optional_decimal(_lookup(result, ("going_in_cap", "going_in_cap_rate")))
    exit_cap = _optional_decimal(_lookup(result, ("exit_cap", "exit_cap_rate")))
    price_per_unit = _optional_decimal(_lookup(result, ("price_per_unit",)))
    min_dscr = _optional_decimal(_lookup(result, ("min_dscr", "minimum_dscr")))
    year1_noi = _optional_decimal(
        _lookup(result, ("year1_noi", "year_1_noi", "projected_noi_used_for_sizing"))
    )
    return {
        "strike": strike,
        "going_in_cap": going_in_cap,
        "exit_cap": exit_cap,
        "price_per_unit": price_per_unit,
        "units": units,
        "min_dscr": min_dscr,
        "year1_noi": year1_noi,
    }


def _breaches(engine: dict[str, Any]) -> list[dict[str, str]]:
    breaches: list[dict[str, str]] = []
    for field in ("going_in_cap", "exit_cap"):
        value = engine[field]
        if value is None:
            continue
        if value < CAP_LOW or value > CAP_HIGH:
            breaches.append(
                {
                    "field": field,
                    "value": format(value, "f"),
                    "band": "[0.06, 0.12]",
                }
            )
    price_per_unit = engine["price_per_unit"]
    if price_per_unit is None and engine["strike"] is not None and engine["units"]:
        price_per_unit = engine["strike"] / Decimal(engine["units"])
    if price_per_unit is not None and (
        price_per_unit < PRICE_PER_UNIT_LOW or price_per_unit > PRICE_PER_UNIT_HIGH
    ):
        breaches.append(
            {
                "field": "price_per_unit",
                "value": format(price_per_unit, "f"),
                "band": "[25000, 750000]",
            }
        )
    min_dscr = engine["min_dscr"]
    if min_dscr is not None and min_dscr < DSCR_FLOOR:
        breaches.append(
            {
                "field": "min_dscr",
                "value": format(min_dscr, "f"),
                "band": ">=1.10",
            }
        )
    year1_noi = engine["year1_noi"]
    priced = engine["strike"] is not None or engine["price_per_unit"] is not None
    if year1_noi is not None and year1_noi < 0 and priced:
        breaches.append(
            {
                "field": "year1_noi",
                "value": format(year1_noi, "f"),
                "band": "non_negative_on_a_price",
            }
        )
    return breaches


def _lookup(result: dict[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in result and result[key] is not None:
            return result[key]
    policy = result.get("policy_summary")
    if isinstance(policy, dict):
        for key in keys:
            if key in policy and policy[key] is not None:
                return policy[key]
    dscr = result.get("dscr")
    if isinstance(dscr, dict) and ("min_dscr" in keys or "minimum_dscr" in keys):
        if dscr.get("minimum") is not None:
            return dscr.get("minimum")
    return None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "Engine figures must be decimal amounts. Refusing to rewrite them.",
        )
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        elif isinstance(value, float):
            parsed = Decimal(str(value))
        else:
            parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "Engine figure is not a decimal amount.",
        ) from exc
    if not parsed.is_finite():
        raise HarnessError(IMPLICIT_ZERO_FORBIDDEN, "Engine figure is not finite.")
    return parsed


def _optional_int(value: object) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise HarnessError(IMPLICIT_ZERO_FORBIDDEN, "Unit count must be an integer.")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise HarnessError(IMPLICIT_ZERO_FORBIDDEN, "Unit count must be an integer.") from exc
    return parsed
