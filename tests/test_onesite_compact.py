"""Synthetic-only contract tests for Compact OneSite XLS/BIFF adapter.

Embedded BIFF8 bytes contain NO private deal sources or live tenant data.
All fixtures are synthetic with explicit canary strings to test PII redaction.
"""
from __future__ import annotations

import base64
import builtins
import hashlib
import io
import json
import traceback
import warnings
import zlib
from types import SimpleNamespace

import pytest

from plat_harness.ingest.onesite_compact import (
    BASE_RENT_CODES,
    ANCILLARY_CATEGORIES,
    ANCILLARY_CODES,
    classify_charge,
    is_base_rent,
    is_ancillary_charge,
    normalize_onesite_compact_xls,
)
from plat_harness.ingest.pms_normalizer import RentRollNormalizationError

MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
NULL_COUNTS = dict.fromkeys(("occupied", "vacant", "down", "total"))
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

CANARIES = (
    "SYNTHETIC CANARY RESIDENT",
    "Synthetic Canary Tenant 101",
    "Synthetic Canary Tenant 102",
    "Synthetic Applicant Canary",
    "Synthetic Future Canary",
    "SECRET_PII_DATA",
)

# Synthetic BIFF8 generated via xlwt 1.3.0
SYNTHETIC_COMPACT_BIFF_ZLIB = (
    "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"
    "ccD7nVSZz20rRlfs6q/Giv3fXEmcd9j/n6KYeUg="
)


def fixture_raw() -> bytes:
    return zlib.decompress(base64.b64decode(SYNTHETIC_COMPACT_BIFF_ZLIB))


def fixture_rows() -> list[list]:
    xlrd = pytest.importorskip("xlrd")
    book = xlrd.open_workbook(file_contents=fixture_raw(), logfile=io.StringIO())
    sheet = book.sheet_by_index(0)
    rows = [sheet.row_values(r) for r in range(sheet.nrows)]
    book.release_resources()
    return rows


def fake_book(monkeypatch, changes=(), *, extra_sheets=0, nrows=None, ncols=24):
    import xlrd

    monkeypatch.undo()
    rows = fixture_rows()
    for r, c, value in changes:
        while len(rows) < r:
            rows.append([""] * ncols)
        while len(rows[r - 1]) < c:
            rows[r - 1].append("")
        rows[r - 1][c - 1] = value

    class Sheet:
        def __init__(self):
            self.ncols = ncols
            self.nrows = nrows if nrows is not None else len(rows)

        def cell_value(self, r, c):
            if r < len(rows) and c < len(rows[r]):
                return rows[r][c]
            return ""

        def cell_type(self, r, c):
            val = self.cell_value(r, c)
            if isinstance(val, (int, float)):
                return 2
            if val:
                return 1
            return 0

    sheet = Sheet()
    book = SimpleNamespace(
        nsheets=1 + extra_sheets,
        sheet_by_index=lambda i: sheet,
        release_resources=lambda: None,
    )
    monkeypatch.setattr(xlrd, "open_workbook", lambda **kwargs: book)
    return fixture_raw()


def codes(result: dict) -> set[str]:
    return {i["code"] for i in result["issues"]}


def observations(result: dict) -> list[dict]:
    return [i["observation"] for i in result["issues"] if i["code"] == "UNRESOLVED_UNIT_USE"]


def check_citations(result: dict, raw: bytes) -> None:
    digest = hashlib.sha256(raw).hexdigest()

    def _walk(item):
        if isinstance(item, dict):
            if {"sheet", "row", "row_end", "column"} <= item.keys():
                assert item["source_sha256"] == digest
                assert item["sheet"] == 1
                assert 1 <= item["row"] <= item["row_end"]
                assert 1 <= item["column"] <= 128
            for val in item.values():
                _walk(val)
        elif isinstance(item, list):
            for val in item:
                _walk(val)

    _walk(result)


def test_positive_compact_onesite_extraction():
    raw = fixture_raw()
    assert raw.startswith(MAGIC)
    result = normalize_onesite_compact_xls(raw)

    assert set(result) == ROOT_KEYS
    assert result["version"] == "1.0"
    assert result["pms_type"] == "realpage"
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["status"] == "blocked"
    assert result["residential_units"] == []
    assert result["commercial_units"] == []
    assert result["counts"] == {s: NULL_COUNTS for s in ("residential", "commercial")}
    assert result["summary"] == {
        s: {"status": "absent", "reported_counts": None, "citations": []}
        for s in ("residential", "commercial")
    }

    unresolved = observations(result)
    assert len(unresolved) == 4

    u101, u102, u103, u104 = unresolved

    # Unit 101: Occupied, Floorplan 1A, Designation N/A, SQFT 850
    assert u101["unit_id"] == "101"
    assert u101["status"] == "occupied"
    assert u101["unit_type"] is None
    assert u101["tenant_name"] == "[REDACTED]"
    # Primary evidence + 5 ancillary charges (TRASH, GARAGE, DAMAGE WAIVER, PET, STORAGE)
    assert len(u101["evidence"]) == 6
    assert "charge" in u101["evidence"][0]  # Base rent (RENT)
    assert u101["evidence"][0]["unit_id"]["row"] == 7
    assert u101["evidence"][0]["unit_id"]["column"] == 3
    assert u101["evidence"][0]["status"]["row"] == 7
    assert u101["evidence"][0]["status"]["column"] == 7
    assert u101["evidence"][0]["floorplan"]["column"] == 4
    assert u101["evidence"][0]["designation"]["column"] == 5
    assert u101["evidence"][0]["tenant_name"]["column"] == 8

    # Unit 102: Occupied-NTV maps to occupied
    assert u102["unit_id"] == "102"
    assert u102["status"] == "occupied"
    assert u102["unit_type"] is None
    assert u102["tenant_name"] == "[REDACTED]"
    # Primary + 2 ancillary charges (TRASH, DAMAGE WAIVER)
    assert len(u102["evidence"]) == 3

    # Unit 103: Vacant
    assert u103["unit_id"] == "103"
    assert u103["status"] == "vacant"
    assert u103["unit_type"] is None
    assert len(u103["evidence"]) == 1

    # Unit 104: Down
    assert u104["unit_id"] == "104"
    assert u104["status"] == "down"
    assert u104["unit_type"] is None
    assert len(u104["evidence"]) == 1

    # Exclusions and warnings
    all_codes = codes(result)
    assert "UNRESOLVED_UNIT_USE" in all_codes
    assert "DOWN_EVIDENCE_UNRESOLVED" in all_codes
    assert "NON_CURRENT_LEASE_EXCLUDED" in all_codes
    assert "NON_CURRENT_SECTION_EXCLUDED" in all_codes
    assert "CONTINUATION_EVIDENCE" in all_codes

    # Citations validation
    check_citations(result, raw)

    # PII sanitization: no canary strings anywhere in serialized result
    encoded = json.dumps(result)
    for canary in CANARIES:
        assert canary not in encoded


def test_charge_classification_helpers():
    assert is_base_rent("RENT") is True
    assert is_base_rent("Lease Rent") is True
    assert is_base_rent("Base Rent") is True
    assert is_base_rent("Market Rent") is True
    assert is_base_rent("TRASH") is False
    assert is_base_rent("GARAGE") is False

    assert is_ancillary_charge("TRASH") is True
    assert is_ancillary_charge("GARAGE") is True
    assert is_ancillary_charge("PET FEE") is True
    assert is_ancillary_charge("STORAGE") is True
    assert is_ancillary_charge("DAMAGE WAIVER PROGRAM") is True
    assert is_ancillary_charge("RENT") is False

    assert classify_charge("TRASH") == "utilities"
    assert classify_charge("GARAGE") == "parking"
    assert classify_charge("PET FEE") == "pet"
    assert classify_charge("STORAGE") == "storage"
    assert classify_charge("DAMAGE WAIVER PROGRAM") == "insurance"
    assert classify_charge("RENT") == "base_rent"
    assert classify_charge(None) == "unknown"


@pytest.mark.parametrize(
    "payload",
    [b"", b"Unit,Status\n101,Occupied", b"PK\x03\x04bad_zip", "not_bytes", None],
)
def test_requires_original_binary_xls_magic(payload):
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(payload)
    assert exc_info.value.code in ("UNSUPPORTED_XLS_FORMAT", "MALFORMED_INPUT")
    assert exc_info.value.__context__ is exc_info.value.__cause__ is None


def test_corrupt_biff_is_sanitized():
    raw = MAGIC + b"GARBAGE_PAYLOAD_" + CANARIES[0].encode()
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(raw)
    assert exc_info.value.code == "MALFORMED_INPUT"
    assert exc_info.value.__context__ is exc_info.value.__cause__ is None
    assert CANARIES[0] not in "".join(traceback.format_exception(exc_info.value))


def test_multi_sheet_refused(monkeypatch):
    raw = fake_book(monkeypatch, extra_sheets=1)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(raw)
    assert exc_info.value.code == "UNSUPPORTED_ONESITE_LAYOUT"


def test_detailed_onesite_layout_refused(monkeypatch):
    # Detailed OneSite has 51+ columns
    raw = fake_book(monkeypatch, ncols=51)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(raw)
    assert exc_info.value.code == "UNSUPPORTED_ONESITE_LAYOUT"


@pytest.mark.parametrize(
    "change",
    [
        (2, 1, "Different Report Title"),
        (6, 3, "NotUnit"),
        (6, 4, "NotFloorplan"),
        (6, 6, "NotSqft"),
        (6, 7, "NotStatus"),
        (6, 8, "NotName"),
    ],
)
def test_missing_or_corrupt_headers_refused(monkeypatch, change):
    raw = fake_book(monkeypatch, [change])
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(raw)
    assert exc_info.value.code == "UNSUPPORTED_ONESITE_LAYOUT"


def test_unsupported_unit_status_blocks_completeness(monkeypatch):
    # Change unit 101 status to 'Model'
    raw = fake_book(monkeypatch, [(7, 7, "Model")])
    result = normalize_onesite_compact_xls(raw)
    assert "UNSUPPORTED_UNIT_STATUS" in codes(result)
    u101 = observations(result)[0]
    assert u101["status"] is None


def test_duplicate_unit_conflict(monkeypatch):
    # Repeat unit 101 with a different status
    raw = fake_book(monkeypatch, [(8, 3, "101"), (8, 7, "Vacant")])
    result = normalize_onesite_compact_xls(raw)
    assert "DUPLICATE_UNIT_CONFLICT" in codes(result)


def test_duplicate_unit_evidence(monkeypatch):
    # Repeat unit 101 with the same status
    raw = fake_book(monkeypatch, [(8, 3, "101"), (8, 7, "Occupied")])
    result = normalize_onesite_compact_xls(raw)
    assert "DUPLICATE_UNIT_EVIDENCE" in codes(result)


def test_orphan_continuation(monkeypatch):
    # Add a charge before the first unit (row 6 is headers, row 7 is unit 101)
    # Insert a blank unit continuation at row 6 with charges
    raw = fake_book(monkeypatch, [(7, 3, ""), (7, 4, ""), (7, 6, ""), (7, 7, ""), (7, 8, "")])
    result = normalize_onesite_compact_xls(raw)
    assert "ORPHAN_CONTINUATION" in codes(result)


def test_missing_inventory_boundary(monkeypatch):
    # Remove 'Totals:' at row 16
    raw = fake_book(monkeypatch, [(16, 1, "")])
    result = normalize_onesite_compact_xls(raw)
    assert "INVENTORY_BOUNDARY_MISSING" in codes(result)


def test_incomplete_unit_row(monkeypatch):
    # Blank floorplan on unit 101
    raw = fake_book(monkeypatch, [(7, 4, "")])
    result = normalize_onesite_compact_xls(raw)
    assert "INCOMPLETE_UNIT_ROW" in codes(result)


def test_limits_enforced(monkeypatch):
    # Max rows exceeded
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(fake_book(monkeypatch, nrows=50001))
    assert exc_info.value.code == "INPUT_LIMIT_EXCEEDED"

    # Max cols exceeded
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(fake_book(monkeypatch, ncols=129))
    assert exc_info.value.code == "INPUT_LIMIT_EXCEEDED"

    # Max cells exceeded (nrows * ncols > 250_000)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(fake_book(monkeypatch, nrows=5000, ncols=51))
    assert exc_info.value.code in ("INPUT_LIMIT_EXCEEDED", "UNSUPPORTED_ONESITE_LAYOUT")

    # Max bytes exceeded (>32 MiB)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(MAGIC + b"0" * (32 * 1024 * 1024 + 1))
    assert exc_info.value.code == "INPUT_LIMIT_EXCEEDED"


def test_former_residents_and_continuation_handling(monkeypatch):
    # Change row 11 to Former resident, followed by a continuation row
    raw = fake_book(
        monkeypatch,
        [
            (11, 7, "Former resident"),
            (12, 19, 30.0),  # Continuation following former resident
        ],
    )
    result = normalize_onesite_compact_xls(raw)
    all_codes = codes(result)
    assert "NON_CURRENT_LEASE_EXCLUDED" in all_codes
    assert "NON_CURRENT_CONTINUATION_EXCLUDED" in all_codes


def test_no_current_units_blocks_completeness(monkeypatch):
    # Set unit 101, 102, 103, 104 all to Applicant
    raw = fake_book(
        monkeypatch,
        [
            (7, 7, "Applicant"),
            (8, 7, "Applicant"),
            (9, 7, "Applicant"),
            (10, 7, "Applicant"),
        ],
    )
    result = normalize_onesite_compact_xls(raw)
    assert "NO_CURRENT_UNITS" in codes(result)
    assert len(observations(result)) == 0


def test_blank_unit_continuation_row(monkeypatch):
    # Insert a blank unit continuation under unit 101 at row 8 with an extra charge
    raw = fake_book(
        monkeypatch,
        [
            (8, 3, ""),  # Blank unit ID
            (8, 4, ""),  # Blank floorplan
            (8, 6, ""),  # Blank sqft
            (8, 7, ""),  # Blank status
            (8, 8, ""),  # Blank name
            (8, 22, 40.0),  # PET FEE charge continuation
        ],
    )
    result = normalize_onesite_compact_xls(raw)
    u101 = observations(result)[0]
    assert u101["unit_id"] == "101"
    assert "CONTINUATION_EVIDENCE" in codes(result)


def test_missing_dependency_is_lazy_and_sanitized(monkeypatch):
    original = builtins.__import__

    def no_xlrd(name, *args, **kwargs):
        if name == "xlrd":
            raise ImportError(CANARIES[0])
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_xlrd)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(fixture_raw())
    assert exc_info.value.code == "XLS_DEPENDENCY_MISSING"
    assert exc_info.value.__context__ is exc_info.value.__cause__ is None


@pytest.mark.parametrize("kind", ["warning", "exception", "log"])
def test_library_diagnostics_are_not_exposed(monkeypatch, capsys, kind):
    import xlrd

    def bad_reader(**kwargs):
        if kind == "warning":
            warnings.warn(CANARIES[0])
        if kind == "log":
            kwargs["logfile"].write(CANARIES[0])
        raise ValueError(CANARIES[0])

    monkeypatch.setattr(xlrd, "open_workbook", bad_reader)
    with pytest.raises(RentRollNormalizationError) as exc_info:
        normalize_onesite_compact_xls(fixture_raw())
    assert exc_info.value.code == "MALFORMED_INPUT"
    assert exc_info.value.__context__ is exc_info.value.__cause__ is None
    assert CANARIES[0] not in "".join(traceback.format_exception(exc_info.value))
    assert CANARIES[0] not in "".join(capsys.readouterr())


def test_repro_lease_id_not_cited_as_rent_charge():
    raw = fixture_raw()
    result = normalize_onesite_compact_xls(raw)
    u101 = [
        issue["observation"]
        for issue in result["issues"]
        if issue["code"] == "UNRESOLVED_UNIT_USE" and issue["observation"]["unit_id"] == "101"
    ][0]
    charge_citation = u101["evidence"][0].get("charge")
    assert charge_citation is not None
    assert charge_citation["column"] in (17, 18)


def test_repro_pet_rent_classified_as_pet_ancillary():
    cat = classify_charge("Pet Rent")
    assert cat == "pet"
    assert is_base_rent("Pet Rent") is False
    assert is_ancillary_charge("Pet Rent") is True


def test_repro_free_rent_classified_as_concessions():
    cat = classify_charge("Free Rent")
    assert cat == "concessions"
    assert is_base_rent("Free Rent") is False
    assert is_ancillary_charge("Free Rent") is True


def test_repro_non_charge_lease_headers_not_base_rent():
    for non_charge_code in ["Lease ID", "Lease Start", "Lease End"]:
        assert is_base_rent(non_charge_code) is False
        assert classify_charge(non_charge_code) == "unknown"
