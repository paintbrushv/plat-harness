import pytest

from plat_harness.errors import CITATION_REQUIRED, CONFLICT_UNRESOLVED, HarnessError, UNCERTIFIED_METRIC
from plat_harness.middleware import Citation, check_citations, check_metric_consistency


def test_metric_consistency_blocks_untagged_conflict(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        check_metric_consistency(["noi"], glossary=glossary)
    assert caught.value.code == CONFLICT_UNRESOLVED


def test_metric_consistency_allows_tagged_conflict(glossary) -> None:
    check_metric_consistency(["noi"], context="ops_actuals", glossary=glossary)


def test_metric_consistency_blocks_forge(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        check_metric_consistency(["irr"], context="forge", glossary=glossary)
    assert caught.value.code == UNCERTIFIED_METRIC


def test_citation_refuses_number_without_source() -> None:
    with pytest.raises(HarnessError) as caught:
        check_citations("T12 R&M was $18,400 in May.", [])
    assert caught.value.code == CITATION_REQUIRED


def test_citation_accepts_complete_source() -> None:
    check_citations(
        "T12 R&M was $18,400 in May.",
        [Citation(artifact="t12.csv", row="R&M", period="2026-05", value="18400")],
    )


def test_citation_allows_prose_without_numbers() -> None:
    check_citations("Ask the PM whether this is one-time repair or deferred maintenance.", None)
