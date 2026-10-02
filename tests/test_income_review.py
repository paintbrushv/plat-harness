"""Synthetic acceptance evidence for booked-income and rent-roll reconciliation."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from plat_harness.cli import main
from plat_harness.income_review import IncomeReviewError, review_income_case


CASE_PATH = Path(__file__).resolve().parents[1] / "samples/deals/synthetic_income_quality/case.json"


@pytest.fixture
def case() -> dict:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def test_synthetic_review_reconciles_booked_income_without_double_losses(case) -> None:
    result = review_income_case(case)
    assert result["schema_version"] == "income-review/1.0.0"
    assert result["periods"]["shared_months"] == 11
    assert result["income"]["t12_net_rent"] == "1905000.00"
    assert result["income"]["t12_other_income"] == "206400.00"
    assert result["income"]["house_other_income"] == "128880.00"
    assert result["income"]["paired_expense_retained"] == "132000.00"
    assert result["income"]["concession_topup"] == "4000.00"
    assert result["operating"]["t12_noi"] == "1011400.00"
    assert result["operating"]["house_base_noi"] == "877880.00"
    assert result["operating"]["house_base_after_reserve"] == "717880.00"
    assert result["operating"]["current_roll_spot_noi"] == "746880.00"
    assert result["operating"]["recent_rent_stress_noi"] == "832880.00"
    assert result["turnover_signal"]["share_of_occupied"] == "0.50"
    assert result["pricing_certified"] is False
    assert "POST_SALE_TAX_UNVERIFIED" in result["blockers"]
    assert "NEGATIVE_TAX_MONTH_REQUIRES_GL_BRIDGE" in result["blockers"]
    assert "FEE_CASH_COLLECTIONS_UNVERIFIED" in result["blockers"]
    assert all(row["collections_verified"] is False for row in result["income"]["other_income_detail"])


def test_fee_credit_revision_changes_noi_by_only_its_explicit_amount(case) -> None:
    old = deepcopy(case)
    for row in old["t12"]["other_income"]:
        if row["code"] == "termination":
            row["credit_rate"] = "0.25"
    previous = review_income_case(old)
    current = review_income_case(case)
    assert Decimal(current["operating"]["house_base_noi"]) - Decimal(
        previous["operating"]["house_base_noi"]
    ) == Decimal("24000")
    assert previous["income"]["t12_other_income"] == current["income"]["t12_other_income"]


def test_missing_month_or_tax_reconciliation_refuses(case) -> None:
    case["t12"]["net_rent_monthly"].pop()
    with pytest.raises(IncomeReviewError, match="exactly 12"):
        review_income_case(case)
    case["t12"]["net_rent_monthly"].append("155000")
    case["t12"]["tax"] = "199999"
    with pytest.raises(IncomeReviewError, match="monthly tax"):
        review_income_case(case)


def test_reported_t12_and_paired_expenses_require_a_bridge(case) -> None:
    case["t12"]["reported_other_income"] = "206401"
    with pytest.raises(IncomeReviewError, match="other-income detail"):
        review_income_case(case)
    case["t12"]["reported_other_income"] = "206400"
    case["t12"]["other_income"][0].pop("paired_expense_evidence")
    with pytest.raises(IncomeReviewError, match="paired expense"):
        review_income_case(case)


def test_cannot_haircut_negative_bad_debt_or_label_private_data_synthetic(case) -> None:
    for row in case["t12"]["other_income"]:
        if row["kind"] == "bad_debt":
            row["credit_rate"] = "0.50"
    with pytest.raises(IncomeReviewError, match="retained in full"):
        review_income_case(case)
    case["data_class"] = "seller_private"
    with pytest.raises(IncomeReviewError, match="explicitly synthetic"):
        review_income_case(case)


def test_review_income_cli_emits_same_provisional_numbers(capsys) -> None:
    assert main(["review-income", "--case", str(CASE_PATH)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["operating"]["house_base_noi"] == "877880.00"
    assert result["pricing_certified"] is False


def test_fractional_unit_count_refuses_instead_of_truncating(case) -> None:
    case["rent_roll"]["occupied"] = "144.5"
    with pytest.raises(IncomeReviewError, match="nonnegative integer"):
        review_income_case(case)


def test_unsupported_schema_version_refuses(case) -> None:
    case["schema_version"] = "income-review/9.0.0"
    with pytest.raises(IncomeReviewError, match="schema_version"):
        review_income_case(case)
