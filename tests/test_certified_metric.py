import pytest

from plat_harness.errors import (
    CONFLICT_UNRESOLVED,
    HarnessError,
    IMPLICIT_ZERO_FORBIDDEN,
    MISSING_MILLAGE,
    OCCUPANCY_COUNTS_REQUIRED,
    RANK_FORBIDDEN,
    UNCERTIFIED_METRIC,
)
from plat_harness.ranks import PermissionRank
from plat_harness.tools.catalog import require_rank
from plat_harness.tools.certified_metric import get_certified_metric
from plat_harness.tools.stubs import call_stub


def test_conflict_without_context_refuses(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("noi", glossary=glossary)
    assert caught.value.code == CONFLICT_UNRESOLVED
    assert "noi" in caught.value.details["metric_id"]


def test_forge_context_never_certified(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("irr", context="forge", glossary=glossary)
    assert caught.value.code == UNCERTIFIED_METRIC


def test_unknown_work_orders_refuse(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("work_order_aging", glossary=glossary)
    assert caught.value.code == UNCERTIFIED_METRIC


def test_crime_flood_employer_refuse_without_feed(glossary) -> None:
    for metric_id in ("crime_index", "flood_zone", "employer_concentration"):
        with pytest.raises(HarnessError) as caught:
            get_certified_metric(metric_id, glossary=glossary)
        assert caught.value.code == UNCERTIFIED_METRIC


def test_missing_millage_refuses(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("millage_rate", glossary=glossary)
    assert caught.value.code == MISSING_MILLAGE


def test_millage_with_rate_returns_decimal_string(glossary) -> None:
    result = get_certified_metric(
        "millage_rate",
        millage_rate_mills="25.31",
        glossary=glossary,
    )
    assert result["value"] == "25.31"
    assert result["unit"] == "mills_per_1000"
    assert result["certification"] == "CERTIFIED"


def test_zero_millage_refuses(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("millage_rate", millage_rate_mills="0", glossary=glossary)
    assert caught.value.code == MISSING_MILLAGE


def test_occupancy_without_counts_refuses(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric(
            "physical_occupancy",
            context="ops_actuals",
            glossary=glossary,
        )
    assert caught.value.code == OCCUPANCY_COUNTS_REQUIRED


def test_occupancy_with_counts_emits_denominator(glossary) -> None:
    result = get_certified_metric(
        "physical_occupancy",
        context="ops_actuals",
        occupied=72,
        vacant=6,
        down=2,
        denominator=80,
        period="2026-06",
        glossary=glossary,
    )
    assert result["occupied"] == 72
    assert result["vacant"] == 6
    assert result["down"] == 2
    assert result["denominator"] == 80
    assert result["value"] == 0.9
    assert result["period"] == "2026-06"


def test_occupancy_zero_denominator_forbidden(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric(
            "physical_occupancy",
            context="ops_actuals",
            occupied=0,
            vacant=0,
            down=0,
            denominator=0,
            glossary=glossary,
        )
    assert caught.value.code == IMPLICIT_ZERO_FORBIDDEN


def test_live_metric_without_backend_does_not_invent(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("egi", context="uw_proforma", glossary=glossary)
    assert caught.value.code == UNCERTIFIED_METRIC


def test_market_rent_without_four_pack_uncertified(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric("market_rent", glossary=glossary)
    assert caught.value.code == UNCERTIFIED_METRIC


def test_coc_without_engine_does_not_invent(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric(
            "cash_on_cash",
            context="uw_proforma",
            asset_or_deal_id="example_garden_style",
            glossary=glossary,
        )
    assert caught.value.code == UNCERTIFIED_METRIC


def test_underwrite_stub_requires_millage() -> None:
    with pytest.raises(HarnessError) as caught:
        call_stub("run_underwriting_model", PermissionRank.DRAFT)
    assert caught.value.code == MISSING_MILLAGE


def test_rank_zero_cannot_request_approval() -> None:
    with pytest.raises(HarnessError) as caught:
        require_rank("request_approval", PermissionRank.EXPLAIN)
    assert caught.value.code == RANK_FORBIDDEN
