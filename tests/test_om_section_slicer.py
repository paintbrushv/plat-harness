"""Synthetic-only contract tests for the OM financial-section slicer.

All test PDFs are generated in-memory via reportlab. No real or private deal
bytes; broker figures stay broker claims; no numeric synthesis is performed.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import types

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from plat_harness.ingest.om_section_slicer import (
    DEFAULT_PAGE_BUDGET,
    MAX_PAGES,
    FINANCIAL_SECTION_CODES,
    financial_section_codes,
    slice_om_sections,
)
from plat_harness.ingest.pms_normalizer import RentRollNormalizationError

CANARIES = (
    "SYNTHETIC CANARY RESIDENT",
    "John Doe Canary",
    "canary_user@example.invalid",
    "+1-555-019-9999",
    "SECRET_PII_NOTE",
)

ROOT_KEYS = {
    "version",
    "source_sha256",
    "total_pages",
    "sections",
    "selected_pages",
    "ocr_required_pages",
    "status",
    "issues",
}

SECTION_KEYS = {
    "code",
    "heading_text",
    "heading_citation",
    "page_start",
    "page_end",
    "text_lines",
    "attribution",
    "provenance",
}


def build_synthetic_pdf(pages_lines: list[list[str]]) -> bytes:
    """Build a multi-page PDF from a list of line strings per page."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for lines in pages_lines:
        y = 750
        for line in lines:
            c.drawString(30, y, line)
            y -= 14
        c.showPage()
    c.save()
    return buf.getvalue()


def check_citations(obj, raw_bytes: bytes, total_pages: int) -> None:
    expected_sha = hashlib.sha256(raw_bytes).hexdigest()
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if {"sheet", "row", "row_end", "column"}.issubset(cur.keys()):
                assert cur["source_sha256"] == expected_sha
                assert 1 <= cur["sheet"] <= total_pages
                assert cur["row"] >= 1 and cur["row_end"] >= cur["row"]
                assert cur["column"] >= 1
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def assert_no_canaries(obj) -> None:
    """Assert canaries stay absent from serialized results and issue messages."""
    dumped = json.dumps(obj, default=str)
    for canary in CANARIES:
        assert canary not in dumped
        assert canary.lower() not in dumped.lower()


def norm(s: str) -> str:
    return " ".join(s.split())


# --- happy path ---------------------------------------------------------


def test_toc_directs_selection_to_actual_table_pages():
    """TOC entries must resolve to the actual heading pages, not the TOC page."""
    pdf = build_synthetic_pdf([
        [
            "Offering Memorandum",
            "Synthetic Property",
            "Table of Contents",
            "..... 4   Operating Statements",
            "..... 6   Rent Roll Summary",
        ],
        ["Property Overview", "The subject property is a synthetic asset."],
        ["Market Overview", "Synthetic submarket data."],
        [
            "Operating Statements",
            "INCOME",
            "Gross Rental Income   1,000,000",
            "Net Operating Income   500,000",
        ],
        ["Rehab Capital Plan", "Synthetic scope description."],
        [
            "Rent Roll Summary",
            "Total Units  100",
        ],
    ])
    result = slice_om_sections(pdf, toc_pages={1})
    assert ROOT_KEYS.issubset(result.keys())
    assert result["status"] == "ok"
    codes = [s["code"] for s in result["sections"]]
    assert codes == ["operating_statements", "rent_roll"]
    ops = result["sections"][0]
    assert SECTION_KEYS.issubset(ops.keys())
    assert ops["heading_citation"]["sheet"] == 4
    rr = result["sections"][1]
    assert rr["heading_citation"]["sheet"] == 6
    # TOC page itself must never be selected as financial context
    assert 1 not in result["selected_pages"]
    assert 4 in result["selected_pages"] and 6 in result["selected_pages"]
    assert result["total_pages"] == 6
    assert result["version"] == "1.0"
    check_citations(result, pdf, 6)
    assert_no_canaries(result)


def test_toc_auto_detected_without_explicit_param():
    """A visible table-of-contents page is auto-detected and never selected."""
    pdf = build_synthetic_pdf([
        [
            "Table of Contents",
            "Executive Summary .... 2",
            "Operating Statements .... 3",
        ],
        ["Executive Summary", "Synthetic narrative."],
        ["Operating Statements", "Gross Rental Income  1,000,000"],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    assert 1 not in result["selected_pages"]
    assert 3 in result["selected_pages"]
    assert [s["code"] for s in result["sections"]] == ["operating_statements"]


def test_absent_toc_still_finds_headings():
    """No table of contents: selection is driven by actual page headings."""
    pdf = build_synthetic_pdf([
        ["Offering Memorandum", "Synthetic Property"],
        ["Property Overview", "Synthetic narrative."],
        [
            "Trailing Twelve Month Operating Statement",
            "Gross Rental Income  1,000,000",
            "Net Operating Income  500,000",
        ],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    assert [s["code"] for s in result["sections"]] == ["operating_statements"]
    assert 3 in result["selected_pages"]


def test_section_carries_original_page_bounds_and_extracted_text():
    pdf = build_synthetic_pdf([
        [
            "Operating Statements",
            "INCOME",
            "Gross Rental Income   1,000,000",
        ],
        ["Net Operating Income   500,000", "EXPENSES", "Taxes  50,000"],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    sec = result["sections"][0]
    assert sec["code"] == "operating_statements"
    assert sec["page_start"] == 1
    assert sec["page_end"] == 2
    lines = [norm(l) for l in sec["text_lines"]]
    assert any("Gross Rental Income" in l and "1,000,000" in l for l in lines)
    assert any("Net Operating Income" in l and "500,000" in l for l in lines)
    assert any("Operating Statements" in l for l in lines)
    # extraction is local text: no numeric transformation, digits preserved verbatim
    assert any("1,000,000" in l for l in lines)
    assert any("50,000" in l and "Taxes" in l for l in lines)
    check_citations(result, pdf, 2)


def test_broker_figures_remain_broker_claims():
    """Selected text carries attribution that figures are broker claims."""
    pdf = build_synthetic_pdf([
        ["Operating Statements", "Gross Rental Income  1,000,000"],
    ])
    result = slice_om_sections(pdf)
    sec = result["sections"][0]
    assert sec["attribution"] == "broker_claim"
    assert sec["provenance"] == "unverified_broker_provided"


def test_repeated_headings_do_not_create_duplicate_sections():
    """Repeated heading lines within a section extend, not duplicate."""
    pdf = build_synthetic_pdf([
        [
            "Operating Statements",
            "Gross Rental Income  1,000,000",
            "Operating Statements",  # repeated band
            "Net Operating Income  500,000",
        ],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    ops = [s for s in result["sections"] if s["code"] == "operating_statements"]
    assert len(ops) == 1
    assert ops[0]["page_start"] == 1 and ops[0]["page_end"] == 1


# --- heading classification ---------------------------------------------


def test_broker_tax_assumptions_classified_as_tax_assumptions():
    """Broker tax assumption sections get their own code, never engine tax."""
    pdf = build_synthetic_pdf([
        [
            "Tax Assumptions",
            "Assessed value  10,000,000",
            "Assumed millage rate  25.31",
        ],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    assert [s["code"] for s in result["sections"]] == ["tax_assumptions"]


def test_mixed_use_lease_terms_selected():
    """Lease/mixed-use term sections are financial context too."""
    pdf = build_synthetic_pdf([
        ["Lease Summary", "Retail Pad  2,500 SF  12/31/2029"],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    assert [s["code"] for s in result["sections"]] == ["lease_terms"]


def test_unrecognized_headings_are_not_selected():
    pdf = build_synthetic_pdf([
        ["Executive Summary", "Synthetic narrative."],
        ["Location and Site Attributes", "Synthetic narrative."],
    ])
    result = slice_om_sections(pdf)
    # no candidate financial sections is an honest blocked outcome, not ok
    assert result["status"] == "blocked"
    assert result["sections"] == []
    assert result["selected_pages"] == []


# --- budget honesty ------------------------------------------------------


def test_budget_overflow_reports_needs_review_not_silent_cut():
    """Exceeding the page budget must surface overflow, not silently drop."""
    pages = [["Operating Statements", "Gross Rental Income  1,000,000"]]
    for i in range(20):
        pages.append([f"Operating Statement Continuation {i}", "Expenses  1,000"])
    pdf = build_synthetic_pdf(pages)
    result = slice_om_sections(pdf, page_budget=5)
    assert result["status"] == "needs_review"
    codes = [i["code"] for i in result["issues"]]
    assert "PAGE_BUDGET_OVERFLOW" in codes
    overflow = next(i for i in result["issues"] if i["code"] == "PAGE_BUDGET_OVERFLOW")
    assert overflow["severity"] == "blocker"
    assert overflow["overflow_pages"] > 0
    # selected pages bounded to the budget
    assert len(result["selected_pages"]) <= 5
    # nothing is silently cut: every candidate page is accounted for explicitly
    assert overflow["total_candidate_pages"] > 5
    assert overflow["overflow_pages"] == overflow["total_candidate_pages"] - len(
        result["selected_pages"]
    )


def test_default_budget_holds_when_generous():
    pages = [["Operating Statements", "Gross Rental Income  1,000,000"]]
    pdf = build_synthetic_pdf(pages)
    result = slice_om_sections(pdf)
    assert result["status"] == "ok"
    assert len(result["selected_pages"]) <= DEFAULT_PAGE_BUDGET


# --- scanned / OCR -------------------------------------------------------


def test_scanned_pages_report_ocr_required():
    """A page with no extractable text is OCR-required, never invented."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(30, 750, "Operating Statements")
    c.drawString(30, 736, "Gross Rental Income  1,000,000")
    c.showPage()
    # scanned page: image content only, no text layer
    c.rect(30, 700, 500, 40)
    c.showPage()
    c.drawString(30, 750, "Net Operating Income  500,000")
    c.showPage()
    c.save()
    pdf = buf.getvalue()

    result = slice_om_sections(pdf)
    assert result["status"] == "incomplete"
    codes = [i["code"] for i in result["issues"]]
    assert "OCR_REQUIRED" in codes
    ocr = next(i for i in result["issues"] if i["code"] == "OCR_REQUIRED")
    assert ocr["severity"] == "warning"
    assert ocr["citation"]["sheet"] == 2
    assert result["ocr_required_pages"] == [2]
    # financial context after the gap is still surfaced, never invented
    assert 3 in result["selected_pages"]
    check_citations(result, pdf, 3)
    assert_no_canaries(result)


def test_no_financial_sections_at_all_blocks():
    pdf = build_synthetic_pdf([
        ["Executive Summary", "Synthetic narrative."],
    ])
    result = slice_om_sections(pdf)
    assert result["status"] == "blocked"
    codes = [i["code"] for i in result["issues"]]
    assert "NO_FINANCIAL_SECTIONS" in codes
    assert result["sections"] == []


# --- error contract -------------------------------------------------------


@pytest.mark.parametrize("bad,code", [
    (None, "MALFORMED_INPUT"),
    (b"", "EMPTY_INPUT"),
    (b"not a pdf", "MALFORMED_INPUT"),
    (b"%PDF- " + b"junk" * 100, "MALFORMED_INPUT"),
])
def test_malformed_inputs_raise_sanitized(bad, code):
    with pytest.raises(RentRollNormalizationError) as exc:
        slice_om_sections(bad)
    assert exc.value.code == code
    assert exc.value.__cause__ is None


def test_page_limit_exceeded():
    pdf = build_synthetic_pdf([[f"Page {i} filler"] for i in range(MAX_PAGES + 1)])
    with pytest.raises(RentRollNormalizationError) as exc:
        slice_om_sections(pdf)
    assert exc.value.code == "INPUT_LIMIT_EXCEEDED"


def test_char_budget_is_input_limit_not_silent_truncation():
    """Total extracted text beyond the char budget is a closed input error."""
    line = "Expenses  1,000 " * 700  # ~10.5k chars per line
    pages = [["Operating Statements"]]
    for _ in range(250):  # ~2.6M extracted chars > MAX_TOTAL_CHARS
        pages.append([line] * 10)
    pdf = build_synthetic_pdf(pages)
    with pytest.raises(RentRollNormalizationError) as exc:
        slice_om_sections(pdf)
    assert exc.value.code == "INPUT_LIMIT_EXCEEDED"


def test_invalid_page_budget_refused():
    pdf = build_synthetic_pdf([["Operating Statements", "Gross Rental Income  1"]])
    with pytest.raises(RentRollNormalizationError) as exc:
        slice_om_sections(pdf, page_budget=0)
    assert exc.value.code == "MALFORMED_INPUT"


# --- sanitization ----------------------------------------------------------


def test_extraction_failure_is_sanitized_no_source_leak(monkeypatch):
    """A pdfplumber blowup mid-document yields a closed error, no bytes leak."""
    # Fault-injection mock: confined to the extraction failure seam only.
    class _Page:
        def __init__(self, fail: bool) -> None:
            self._fail = fail

        def extract_text(self) -> str:
            if self._fail:
                raise RuntimeError("SECRET_PII_NOTE internal failure")
            return "Operating Statements\nGross Rental Income  1,000,000"

    class _Doc:
        def __init__(self) -> None:
            self.pages = [_Page(fail=False), _Page(fail=True)]

        def close(self) -> None:
            return None

    fake = types.ModuleType("pdfplumber")
    fake.open = lambda fh: _Doc()
    monkeypatch.setitem(sys.modules, "pdfplumber", fake)

    pdf = build_synthetic_pdf([
        ["Operating Statements", "Gross Rental Income  1,000,000"],
        ["Net Operating Income  500,000"],
    ])
    with pytest.raises(RentRollNormalizationError) as exc:
        slice_om_sections(pdf)
    assert exc.value.code == "EXTRACTION_FAILURE"
    assert exc.value.__cause__ is None
    assert "SECRET_PII_NOTE" not in str(exc.value)


# --- returned config isolation ---------------------------------------------


def test_registry_copy_is_isolated():
    """Mutating a returned registry copy must not poison later loads."""
    codes1 = financial_section_codes()
    codes1["operating_statements"]["aliases"].add("TAMPER_ALIAS")
    codes2 = financial_section_codes()
    assert "TAMPER_ALIAS" not in codes2["operating_statements"]["aliases"]
    assert "TAMPER_ALIAS" not in FINANCIAL_SECTION_CODES["operating_statements"]["aliases"]


def test_returned_result_state_is_isolated():
    """Mutating a returned result's nested state must not poison later loads."""
    pdf = build_synthetic_pdf([
        ["Operating Statements", "Gross Rental Income  1,000,000"],
    ])
    r1 = slice_om_sections(pdf)
    r1["sections"][0]["text_lines"].append("TAMPERED")
    r1["issues"].append({"code": "TAMPERED"})
    r1["selected_pages"].append(999)
    r2 = slice_om_sections(pdf)
    assert "TAMPERED" not in r2["sections"][0]["text_lines"]
    assert all(i.get("code") != "TAMPERED" for i in r2["issues"])
    assert 999 not in r2["selected_pages"]


def test_toc_pages_argument_excludes_only_toc():
    """toc_pages only excludes TOC pages themselves, never heading pages."""
    pdf = build_synthetic_pdf([
        ["Table of Contents", "Operating Statements .... 3"],
        ["Executive Summary", "Synthetic narrative."],
        ["Operating Statements", "Gross Rental Income  1,000,000"],
    ])
    result = slice_om_sections(pdf, toc_pages={1})
    assert result["status"] == "ok"
    assert 3 in result["selected_pages"]
    assert 1 not in result["selected_pages"]
