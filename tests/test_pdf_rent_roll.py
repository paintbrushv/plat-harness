"""Synthetic-only contract tests for Summit / East Quarter PDF rent roll adapter.

All test PDFs are generated in-memory via reportlab. No live or private deal files.
"""
from __future__ import annotations

import hashlib
import io
import json
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from plat_harness.ingest.pms_normalizer import RentRollNormalizationError
from plat_harness.ingest.pdf_rent_roll import (
    BASE_RENT_CODES,
    ANCILLARY_CATEGORIES,
    ANCILLARY_CODES,
    classify_charge,
    is_base_rent,
    is_ancillary_charge,
    normalize_pdf_rent_roll,
)

CANARIES = (
    "SYNTHETIC CANARY RESIDENT",
    "John Doe Canary",
    "Jane Smith Canary",
    "canary_user@example.invalid",
    "+1-555-019-9999",
    "SECRET_PII_NOTE",
)

ROOT_KEYS = {
    "version",
    "pms_type",
    "source_sha256",
    "residential_units",
    "commercial_units",
    "counts",
    "summary",
    "issues",
    "status",
}


def build_synthetic_pdf(
    pages_lines: list[list[str]],
) -> bytes:
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


def sample_header_page(
    *,
    title: str = "Rent Roll",
    property_name: str = "Synthetic Property (p0001234)",
    as_of: str = "As Of = 05/01/2026",
    month_year: str = "Month Year = 05/2026",
    line_a: str = "Unit Unit Type Name Market Resident Lease Move Out Balance",
    line_b: str = "Sq Ft Rent Rent Deposit Expiration",
    section_header: str = "Current/Notice/Vacant Residents",
    unit_lines: list[str] | None = None,
    extra_lines: list[str] | None = None,
) -> list[str]:
    lines = [
        title,
        property_name,
        as_of,
        month_year,
        line_a,
        line_b,
        section_header,
    ]
    if unit_lines:
        lines.extend(unit_lines)
    if extra_lines:
        lines.extend(extra_lines)
    return lines


def repeated_header_page(
    *,
    title: str = "Rent Roll Page 2",
    property_name: str = "Synthetic Property (p0001234)",
    as_of: str = "As Of = 05/01/2026",
    month_year: str = "Month Year = 05/2026",
    line_a: str = "Unit Unit Type Name Market Resident Lease Move Out Balance",
    line_b: str = "Sq Ft Rent Rent Deposit Expiration",
    unit_lines: list[str] | None = None,
    extra_lines: list[str] | None = None,
) -> list[str]:
    lines = [
        title,
        property_name,
        as_of,
        month_year,
        line_a,
        line_b,
    ]
    if unit_lines:
        lines.extend(unit_lines)
    if extra_lines:
        lines.extend(extra_lines)
    return lines


def check_citations(obj: dict | list, raw_bytes: bytes, max_pages: int = 500) -> None:
    expected_sha = hashlib.sha256(raw_bytes).hexdigest()
    if isinstance(obj, dict):
        if {"sheet", "row", "row_end", "column"}.issubset(obj.keys()):
            assert obj["source_sha256"] == expected_sha
            assert 1 <= obj["sheet"] <= max_pages
            assert 1 <= obj["row"] <= obj["row_end"]
            assert 1 <= obj["column"] <= 128
        for v in obj.values():
            check_citations(v, raw_bytes, max_pages)
    elif isinstance(obj, list):
        for item in obj:
            check_citations(item, raw_bytes, max_pages)


def test_magic_check_non_pdf():
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(b"This is plain text, not a PDF file.")
    assert exc_info.value.code == "MALFORMED_INPUT"


def test_empty_input():
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(b"")
    assert exc_info.value.code == "EMPTY_INPUT"

    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(b"   \n\t  ")
    assert exc_info.value.code == "EMPTY_INPUT"


def test_corrupted_pdf():
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(b"%PDF-1.4\nNOT A VALID PDF BODY GARBAGE \x00\xff")
    assert exc_info.value.code == "MALFORMED_INPUT"


def test_non_bytes_input():
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll("Not bytes")  # type: ignore[arg-type]
    assert exc_info.value.code == "MALFORMED_INPUT"

    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(None)  # type: ignore[arg-type]
    assert exc_info.value.code == "MALFORMED_INPUT"


def test_input_limit_exceeded_bytes():
    # 32 MiB + 1 byte
    oversized = b"%PDF-1.4 " + b"0" * (32 * 1024 * 1024 + 1)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(oversized)
    assert exc_info.value.code == "INPUT_LIMIT_EXCEEDED"


def test_input_limit_exceeded_pages():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(501):
        c.drawString(30, 750, f"Page {i + 1}")
        c.showPage()
    c.save()
    raw = buf.getvalue()

    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(raw)
    assert exc_info.value.code == "INPUT_LIMIT_EXCEEDED"


def test_unsupported_layout_single_page():
    page1 = sample_header_page(
        unit_lines=["101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00"]
    )
    raw = build_synthetic_pdf([page1])
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(raw)
    assert exc_info.value.code == "UNSUPPORTED_PDF_LAYOUT"


def test_unsupported_layout_wrong_title():
    page1 = sample_header_page(
        title="Income Statement",
        unit_lines=["101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00"],
    )
    page2 = repeated_header_page(
        title="Income Statement Page 2",
        unit_lines=["102 2BR 950 Jane Smith 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00"],
    )
    raw = build_synthetic_pdf([page1, page2])
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(raw)
    assert exc_info.value.code == "UNSUPPORTED_PDF_LAYOUT"


def test_unsupported_layout_missing_split_headers():
    page1 = [
        "Rent Roll",
        "Synthetic Asset",
        "As Of = 05/01/2026",
        "Month Year = 05/2026",
        "Current/Notice/Vacant Residents",
        "101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
    ]
    page2 = [
        "Rent Roll Page 2",
        "102 2BR 950 Jane Smith 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
    ]
    raw = build_synthetic_pdf([page1, page2])
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(raw)
    assert exc_info.value.code == "UNSUPPORTED_PDF_LAYOUT"


def test_unsupported_layout_missing_section_header():
    page1 = [
        "Rent Roll",
        "Synthetic Asset",
        "As Of = 05/01/2026",
        "Month Year = 05/2026",
        "Unit Unit Type Name Market Resident Lease Move Out Balance",
        "Sq Ft Rent Rent Deposit Expiration",
        "101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
    ]
    page2 = [
        "Rent Roll Page 2",
        "102 2BR 950 Jane Smith 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
    ]
    raw = build_synthetic_pdf([page1, page2])
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_pdf_rent_roll(raw)
    assert exc_info.value.code == "UNSUPPORTED_PDF_LAYOUT"


def test_positive_split_header_extraction_and_folding():
    p1 = sample_header_page(
        unit_lines=[
            f"101 1BR 750 {CANARIES[0]} 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            f"102 2BR 950 {CANARIES[1]} 1,800.00 1,800.00 600.00 0.00 05/31/2026 05/31/2026 50.00",
        ]
    )
    p2 = repeated_header_page(
        unit_lines=[
            "103 Studio 500 VACANT VACANT 1,200.00 0.00 0.00 0.00 0.00",
            "104 1BR 750 DOWN DOWN 1,400.00 0.00 0.00 0.00 0.00",
        ],
        extra_lines=["Totals: 4 2,950 5,900.00 3,250.00 1,100.00 0.00 50.00"],
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    assert set(result.keys()) == ROOT_KEYS
    assert result["version"] == "1.0"
    assert result["pms_type"] == "realpage"
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()

    # Floorplans "1BR", "2BR", "Studio" do NOT resolve use => explicit UNRESOLVED_UNIT_USE
    assert result["status"] == "blocked"
    assert result["residential_units"] == []
    assert result["commercial_units"] == []

    unresolved_issues = [i for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]
    assert len(unresolved_issues) == 4

    unresolved_obs = [i["observation"] for i in unresolved_issues]
    u101, u102, u103, u104 = unresolved_obs

    assert u101["unit_id"] == "101"
    assert u101["status"] == "occupied"
    assert u101["unit_type"] is None
    assert u101["tenant_name"] == "[REDACTED]"
    assert len(u101["evidence"]) == 1
    ev101 = u101["evidence"][0]
    assert ev101["unit_id"]["sheet"] == 1
    assert ev101["unit_id"]["column"] == 1
    assert ev101["status"]["column"] == 4
    assert ev101["unit_type"]["column"] == 2
    assert ev101["tenant_name"]["column"] == 4
    assert ev101["charge"]["column"] == 6

    assert u102["unit_id"] == "102"
    assert u102["status"] == "occupied"
    assert u102["unit_type"] is None
    assert u102["tenant_name"] == "[REDACTED]"

    assert u103["unit_id"] == "103"
    assert u103["status"] == "vacant"
    assert u103["unit_type"] is None
    assert u103["tenant_name"] == "[REDACTED]"

    assert u104["unit_id"] == "104"
    assert u104["status"] == "down"
    assert u104["unit_type"] is None
    assert u104["tenant_name"] == "[REDACTED]"

    # Citations check
    check_citations(result, raw)

    # PII Check: strictly redacted, no canary leaks
    encoded = json.dumps(result)
    for canary in CANARIES:
        assert canary not in encoded


def test_multi_page_repeated_headers():
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 Occupied Tenant One 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "102 2BR 950 Occupied Tenant Two 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
        ]
    )
    p2 = repeated_header_page(
        unit_lines=[
            "201 1BR 750 Occupied Tenant Three 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "202 2BR 950 Occupied Tenant Four 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
        ]
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    unresolved = [i["observation"] for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]
    assert len(unresolved) == 4
    u_ids = [u["unit_id"] for u in unresolved]
    assert u_ids == ["101", "102", "201", "202"]

    # Verify sheet 1 vs sheet 2 citations
    assert unresolved[0]["evidence"][0]["unit_id"]["sheet"] == 1
    assert unresolved[1]["evidence"][0]["unit_id"]["sheet"] == 1
    assert unresolved[2]["evidence"][0]["unit_id"]["sheet"] == 2
    assert unresolved[3]["evidence"][0]["unit_id"]["sheet"] == 2


def test_status_mapping():
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 Alice Smith 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "102 2BR 950 VACANT VACANT 1,800.00 0.00 0.00 0.00 0.00",
            "103 1BR 700 Vacant Unrented 1,400.00 0.00 0.00 0.00 0.00",
            "104 1BR 700 DOWN DOWN 1,400.00 0.00 0.00 0.00 0.00",
            "105 2BR 900 OFFLINE 1,700.00 0.00 0.00 0.00 0.00",
            "106 1BR 750 MODEL MODEL 1,500.00 0.00 0.00 0.00 0.00",
        ]
    )
    p2 = repeated_header_page(unit_lines=[])
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    # Unit 106 MODEL MODEL emits UNSUPPORTED_STATUS
    codes = [i["code"] for i in result["issues"]]
    assert "UNSUPPORTED_STATUS" in codes

    unresolved = {
        i["observation"]["unit_id"]: i["observation"]
        for i in result["issues"]
        if i["code"] == "UNRESOLVED_UNIT_USE"
    }
    assert unresolved["101"]["status"] == "occupied"
    assert unresolved["102"]["status"] == "vacant"
    assert unresolved["103"]["status"] == "vacant"
    assert unresolved["104"]["status"] == "down"
    assert unresolved["105"]["status"] == "down"
    assert "106" not in unresolved  # skipped due to unsupported status


def test_resolved_residential_and_commercial_use():
    p1 = sample_header_page(
        unit_lines=[
            "101 Residential 800 Alice Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "102 Residential 850 VACANT VACANT 1,550.00 0.00 0.00 0.00 0.00",
        ]
    )
    p2 = repeated_header_page(
        unit_lines=[
            "C1 Commercial 1,200 Bob Commercial 3,000.00 3,000.00 1,000.00 0.00 12/31/2027 0.00",
        ],
        extra_lines=["Totals: 3 2,850 6,050.00 4,450.00 1,500.00 0.00 0.00"],
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    assert result["status"] == "normalized_unvalidated"
    assert len(result["residential_units"]) == 2
    assert len(result["commercial_units"]) == 1

    assert result["counts"]["residential"] == {
        "occupied": 1,
        "vacant": 1,
        "down": 0,
        "total": 2,
    }
    assert result["counts"]["commercial"] == {
        "occupied": 1,
        "vacant": 0,
        "down": 0,
        "total": 1,
    }


def test_charge_separation_and_helpers():
    # Base rent codes
    for code in ("Rent", "RENT", "Base", "BASE", "Base Rent", "Resident Rent", "Actual Rent", "Market Rent"):
        assert is_base_rent(code) is True
        assert is_ancillary_charge(code) is False
        assert classify_charge(code) == "base_rent"

    # Ancillary categories
    for code in ("Pet Rent", "PET", "Pet Fee", "Dog"):
        assert is_base_rent(code) is False
        assert is_ancillary_charge(code) is True
        assert classify_charge(code) == "pet"

    for code in ("Parking", "PARK", "Garage", "Carport"):
        assert is_ancillary_charge(code) is True
        assert classify_charge(code) == "parking"

    for code in ("Storage", "STOR", "Locker"):
        assert is_ancillary_charge(code) is True
        assert classify_charge(code) == "storage"

    assert classify_charge("UNKNOWN_FEE_CODE") == "unknown"
    assert classify_charge("Free Rent") == "concessions"
    assert is_base_rent("Free Rent") is False
    for non_charge in ("Lease ID", "Lease Start", "Lease End"):
        assert is_base_rent(non_charge) is False
        assert classify_charge(non_charge) == "unknown"


def test_unit_deduplication_and_conflicts():
    # Case 1: Same unit repeated with same status -> DUPLICATE_UNIT_EVIDENCE warning
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
        ]
    )
    p2 = repeated_header_page(unit_lines=[])
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    codes = [i["code"] for i in result["issues"]]
    assert "DUPLICATE_UNIT_EVIDENCE" in codes
    unresolved = [i["observation"] for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]
    assert len(unresolved) == 1
    assert len(unresolved[0]["evidence"]) == 2

    # Case 2: Same unit repeated with CONFLICTING status -> DUPLICATE_UNIT_CONFLICT blocker
    p1_conflict = sample_header_page(
        unit_lines=[
            "101 1BR 750 John Doe 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "101 1BR 750 VACANT VACANT 1,500.00 0.00 0.00 0.00 0.00",
        ]
    )
    raw_conflict = build_synthetic_pdf([p1_conflict, p2])
    result_conflict = normalize_pdf_rent_roll(raw_conflict)

    conflict_codes = [i["code"] for i in result_conflict["issues"]]
    assert "DUPLICATE_UNIT_CONFLICT" in conflict_codes
    assert result_conflict["status"] == "blocked"


def test_non_current_section_exclusion():
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 Current Resident One 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
            "102 2BR 950 Current Resident Two 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
        ]
    )
    p2 = repeated_header_page(
        extra_lines=[
            "Future Residents/Applicants",
            "F101 1BR 750 Future Applicant One 1,500.00 0.00 500.00 0.00 01/01/2027 0.00",
            "F102 2BR 950 Future Applicant Two 1,800.00 0.00 500.00 0.00 02/01/2027 0.00",
            "Totals: 2 1,700 3,300.00 3,250.00 1,100.00 0.00 0.00",
        ]
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    codes = [i["code"] for i in result["issues"]]
    assert "NON_CURRENT_SECTION_EXCLUDED" in codes

    unresolved = [i["observation"] for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]
    # Only the 2 current units are retained
    assert len(unresolved) == 2
    u_ids = [u["unit_id"] for u in unresolved]
    assert u_ids == ["101", "102"]


def test_summary_termination():
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 Current Tenant 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
        ],
        extra_lines=[
            "Totals: 1 750 1,500.00 1,450.00 500.00 0.00 0.00",
            "Summary Groups Square Market Actual Security Other",
            "999 1BR 750 Trailing Data Should Be Ignored 1,500.00 1,450.00 0.00 0.00 0.00",
        ],
    )
    p2 = repeated_header_page(
        unit_lines=[
            "998 1BR 750 Trailing Page Should Be Ignored 1,500.00 1,450.00 0.00 0.00 0.00",
        ]
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    unresolved = [i["observation"] for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]
    assert len(unresolved) == 1
    assert unresolved[0]["unit_id"] == "101"


def test_citation_coordinates_verification():
    p1 = sample_header_page(
        unit_lines=[
            "101 1BR 750 Tenant One 1,500.00 1,450.00 500.00 0.00 12/31/2026 0.00",
        ]
    )
    p2 = repeated_header_page(
        unit_lines=[
            "201 2BR 950 Tenant Two 1,800.00 1,800.00 600.00 0.00 05/31/2026 0.00",
        ]
    )
    raw = build_synthetic_pdf([p1, p2])
    result = normalize_pdf_rent_roll(raw)

    expected_sha = hashlib.sha256(raw).hexdigest()
    assert result["source_sha256"] == expected_sha

    # Verify every citation in issues and observations
    for issue in result["issues"]:
        cite = issue["citation"]
        if cite is not None:
            assert cite["source_sha256"] == expected_sha
            assert cite["sheet"] in (1, 2)
            assert 1 <= cite["row"] <= cite["row_end"]
            assert 1 <= cite["column"] <= 11
        if "observation" in issue:
            for ev in issue["observation"]["evidence"]:
                for key, c in ev.items():
                    assert c["source_sha256"] == expected_sha
                    assert c["sheet"] in (1, 2)
                    assert 1 <= c["row"] <= c["row_end"]
                    assert 1 <= c["column"] <= 11


def test_closed_error_codes():
    valid_codes = {
        "MALFORMED_INPUT",
        "INPUT_LIMIT_EXCEEDED",
        "UNSUPPORTED_PDF_LAYOUT",
        "EMPTY_INPUT",
        "PDF_DEPENDENCY_MISSING",
    }
    for bad_input in (
        b"",
        b"not_pdf",
        b"%PDF-1.4 garbage",
        b"%PDF-1.4 " + b"0" * (33 * 1024 * 1024),
    ):
        try:
            normalize_pdf_rent_roll(bad_input)
        except RentRollNormalizationError as exc:
            assert exc.code in valid_codes
