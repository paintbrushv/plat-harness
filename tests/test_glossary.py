from pathlib import Path

from plat_harness.errors import HarnessError, NOT_FOUND
from plat_harness.glossary import load_glossary
from plat_harness.tools.catalog import TOOL_SPECS


def test_glossary_loads_conflicts_without_averaging(glossary) -> None:
    noi = glossary.require("noi")
    assert noi.status == "CONFLICT"
    assert len(noi.definitions) >= 3
    contexts = set(noi.contexts())
    assert "uw_proforma" in contexts
    assert "ops_actuals" in contexts
    assert "governance" in contexts


def test_unknown_metric_not_found(glossary) -> None:
    try:
        glossary.require("not_a_real_metric")
    except HarnessError as exc:
        assert exc.code == NOT_FOUND
    else:
        raise AssertionError("expected NOT_FOUND")


def test_eighteen_tools_registered() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert len(names) == 18
    assert len(set(names)) == 18
    assert names[0] == "get_certified_metric"
    assert names[-1] == "fetch_trace"


def test_load_glossary_round_trip() -> None:
    loaded = load_glossary()
    assert loaded.version
    assert "cash_on_cash" in loaded.metrics
    assert loaded.require("cash_on_cash").status == "CONFLICT"


def test_catalog_does_not_hardcode_seven_percent_hurdle() -> None:
    from plat_harness.tools.catalog import TOOL_SPECS as specs

    blob = " ".join(spec.purpose for spec in specs).lower()
    assert "at 7%" not in blob
    assert "deal_funnel_sop" not in blob


def test_default_policy_is_generic() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "policies" / "default.yaml").read_text(encoding="utf-8")
    assert "coc_hurdle: null" in text
    assert "0.07" not in text
    assert "80–300" not in text and "80-300" not in text
