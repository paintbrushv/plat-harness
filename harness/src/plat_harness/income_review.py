"""Deterministic, source-shaped income review for a synthetic deal.

This is an evidence bridge, not a valuation engine. Amounts are Decimal dollars;
booked GL income is never represented as collected cash.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class IncomeReviewError(ValueError):
    """The review input is incomplete or internally inconsistent."""


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise IncomeReviewError(f"{field} must be a decimal string or integer")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise IncomeReviewError(f"{field} is not a decimal amount") from exc
    if not number.is_finite():
        raise IncomeReviewError(f"{field} must be finite")
    return number


def _months(value: Any, field: str) -> list[Decimal]:
    if not isinstance(value, list) or len(value) != 12:
        raise IncomeReviewError(f"{field} requires exactly 12 monthly values")
    return [_money(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and value.isascii() and value.isdecimal())
    ):
        raise IncomeReviewError(f"{field} must be a nonnegative integer")
    try:
        number = int(value)
    except (ValueError, OverflowError) as exc:
        raise IncomeReviewError(f"{field} must be a nonnegative integer") from exc
    if number < 0:
        raise IncomeReviewError(f"{field} must be a nonnegative integer")
    return number


def _period(value: Any, field: str) -> int:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        raise IncomeReviewError(f"{field} must be YYYY-MM")
    try:
        year, month = (int(part) for part in value.split("-"))
        date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise IncomeReviewError(f"{field} must be YYYY-MM") from exc
    return year * 12 + month


def _fmt(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def review_income_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile monthly booked income, current rent, and explicit house credits.

    The caller supplies the judgmental credit rates. This function validates the
    evidence and applies the rates without adding a second collection deduction.
    """
    if not isinstance(case, Mapping) or case.get("data_class") != "synthetic":
        raise IncomeReviewError("Only an explicitly synthetic case may use this public review")
    if case.get("schema_version") != "income-review/1.0.0":
        raise IncomeReviewError("income review requires schema_version income-review/1.0.0")
    t12 = case["t12"]
    roll = case["rent_roll"]
    house = case["house"]
    end = _period(t12["period_end"], "t12.period_end")
    prior_end = _period(t12["prior_period_end"], "t12.prior_period_end")
    overlap = max(0, 12 - abs(end - prior_end))
    if end <= prior_end:
        raise IncomeReviewError("prior_period_end must precede period_end")

    units = _count(case["units"], "units")
    occupied, vacant, down = (_count(roll[key], f"rent_roll.{key}") for key in ("occupied", "vacant", "down"))
    if units <= 0 or min(occupied, vacant, down) < 0 or occupied + vacant + down != units:
        raise IncomeReviewError("rent-roll occupied, vacant, and down must sum to units")
    recent_moveins = _count(roll["recent_moveins_current_rent_rows"], "rent_roll.recent_moveins_current_rent_rows")
    if not 0 <= recent_moveins <= occupied:
        raise IncomeReviewError("recent current-rent move-ins must be between zero and occupied")

    rent_months = _months(t12["net_rent_monthly"], "t12.net_rent_monthly")
    tax_months = _months(t12["tax_monthly"], "t12.tax_monthly")
    rent = sum(rent_months)
    if rent != _money(t12["reported_net_rent"], "t12.reported_net_rent"):
        raise IncomeReviewError("monthly net rent does not reconcile to reported T12 net rent")
    tax = _money(t12["tax"], "t12.tax")
    if sum(tax_months) != tax:
        raise IncomeReviewError("monthly tax does not reconcile to T12 tax")
    insurance = _money(t12["insurance"], "t12.insurance")
    expenses = _money(t12["operating_expenses"], "t12.operating_expenses")
    if min(tax, insurance, expenses) < 0 or expenses < tax + insurance:
        raise IncomeReviewError("T12 operating expenses must include tax and insurance")

    details: list[dict[str, Any]] = []
    other_t12 = Decimal(0)
    other_credited = Decimal(0)
    positive_t12 = Decimal(0)
    positive_credited = Decimal(0)
    paired_expense = Decimal(0)
    seen: set[str] = set()
    for item in t12["other_income"]:
        code = str(item["code"])
        if code in seen:
            raise IncomeReviewError(f"duplicate other-income code: {code}")
        seen.add(code)
        if item.get("evidence") != "booked_gl":
            raise IncomeReviewError(f"{code}: evidence must identify booked GL income")
        monthly = _months(item["monthly"], f"other_income.{code}.monthly")
        annual = sum(monthly)
        credit = _money(item["credit_rate"], f"other_income.{code}.credit_rate")
        if not Decimal(0) <= credit <= Decimal(1):
            raise IncomeReviewError(f"{code}: credit rate must be between zero and one")
        if item.get("kind") == "bad_debt" and annual >= 0:
            raise IncomeReviewError(f"{code}: booked bad debt must be negative")
        if annual < 0 and credit != 1:
            raise IncomeReviewError(f"{code}: negative booked income must be retained in full")
        cost = _money(item.get("paired_expense", "0"), f"other_income.{code}.paired_expense")
        if cost < 0:
            raise IncomeReviewError(f"{code}: paired expense cannot be negative")
        if cost and item.get("paired_expense_evidence") != "included_in_t12_opex":
            raise IncomeReviewError(f"{code}: paired expense needs T12 operating-expense evidence")
        amount = annual * credit
        other_t12 += annual
        other_credited += amount
        paired_expense += cost
        if annual > 0:
            positive_t12 += annual
            positive_credited += amount
        details.append({
            "code": code,
            "kind": item["kind"],
            "evidence": "booked_gl",
            "collections_verified": False,
            "t12": _fmt(annual),
            "credit_rate": _fmt(credit),
            "house_credit": _fmt(amount),
            "positive_months": sum(value > 0 for value in monthly),
            "negative_months": sum(value < 0 for value in monthly),
            "last_3_annualized": _fmt(sum(monthly[-3:]) * 4),
            "last_6_annualized": _fmt(sum(monthly[-6:]) * 2),
            "paired_expense_retained_in_opex": _fmt(cost),
        })

    house_tax = _money(house["tax"], "house.tax")
    if other_t12 != _money(t12["reported_other_income"], "t12.reported_other_income"):
        raise IncomeReviewError("monthly other-income detail does not reconcile to reported T12")
    house_insurance = _money(house["insurance"], "house.insurance")
    if min(house_tax, house_insurance) < 0:
        raise IncomeReviewError("house tax and insurance must be nonnegative")
    house_opex = expenses - tax - insurance + house_tax + house_insurance
    floor = _money(house["concession_floor"], "house.concession_floor")
    booked_concessions = _money(t12["upfront_concessions"], "t12.upfront_concessions")
    arrears = _money(t12["rental_arrears_writeoffs"], "t12.rental_arrears_writeoffs")
    reserve = _money(house["reserve_per_unit"], "house.reserve_per_unit") * units
    if min(floor, booked_concessions, arrears, reserve) < 0:
        raise IncomeReviewError("concessions, writeoffs, and reserve must be nonnegative")
    # T12 net rent already includes observed concessions and rental writeoffs.
    topup = max(Decimal(0), floor - booked_concessions)
    t12_noi = rent + other_t12 - expenses
    if t12_noi != _money(t12["reported_noi"], "t12.reported_noi"):
        raise IncomeReviewError("T12 revenue and expense do not reconcile to reported NOI")
    base_noi = rent + other_credited - topup - house_opex
    spot_rent = _money(roll["occupied_base_rent_monthly"], "rent_roll.occupied_base_rent_monthly") * 12
    if spot_rent < 0:
        raise IncomeReviewError("occupied base rent must be nonnegative")
    spot_noi = spot_rent + other_credited - arrears - max(floor, booked_concessions) - house_opex
    rent_last3 = sum(rent_months[-3:]) * 4
    recent_rent_stress = base_noi - max(Decimal(0), rent - rent_last3)
    tax_status = house["tax_status"]
    if tax_status not in ("verified_post_sale", "interim_assumption"):
        raise IncomeReviewError("house.tax_status must be verified_post_sale or interim_assumption")
    if tax_status == "verified_post_sale" and not house.get("tax_source"):
        raise IncomeReviewError("verified post-sale tax requires a source")
    blockers = []
    if tax_status != "verified_post_sale":
        blockers.append("POST_SALE_TAX_UNVERIFIED")
    if any(value < 0 for value in tax_months):
        blockers.append("NEGATIVE_TAX_MONTH_REQUIRES_GL_BRIDGE")
    blockers.append("FEE_CASH_COLLECTIONS_UNVERIFIED")

    return {
        "data_class": "synthetic",
        "schema_version": "income-review/1.0.0",
        "case_id": case["case_id"],
        "status": "provisional_evidence_review",
        "pricing_certified": False,
        "blockers": blockers,
        "periods": {
            "current_t12_end": t12["period_end"],
            "prior_t12_end": t12["prior_period_end"],
            "shared_months": overlap,
        },
        "occupancy": {"units": units, "occupied": occupied, "vacant": vacant, "down": down},
        "turnover_signal": {
            "recent_moveins_on_current_rent_rows": recent_moveins,
            "share_of_occupied": _fmt(Decimal(recent_moveins) / Decimal(occupied)) if occupied else None,
            "limitation": "Current-resident lower bound; not a historical turn count or renewal split",
        },
        "income": {
            "t12_net_rent": _fmt(rent),
            "last_3_net_rent_annualized": _fmt(rent_last3),
            "t12_other_income": _fmt(other_t12),
            "house_other_income": _fmt(other_credited),
            "positive_other_income": _fmt(positive_t12),
            "positive_other_income_credited": _fmt(positive_credited),
            "paired_expense_retained": _fmt(paired_expense),
            "concession_topup": _fmt(topup),
            "other_income_detail": details,
        },
        "operating": {
            "t12_noi": _fmt(t12_noi),
            "house_operating_expenses": _fmt(house_opex),
            "house_base_noi": _fmt(base_noi),
            "house_base_after_reserve": _fmt(base_noi - reserve),
            "current_roll_spot_noi": _fmt(spot_noi),
            "recent_rent_stress_noi": _fmt(recent_rent_stress),
            "reserve": _fmt(reserve),
        },
    }
