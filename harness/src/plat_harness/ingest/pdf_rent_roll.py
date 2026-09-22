"""Read-only Summit / East Quarter PDF rent roll adapter; never occupancy approval.

Normalized extraction for Summit / East Quarter multi-page PDF rent rolls with
split header lines, folded columns, and section-scoped parsing.
No raw rows, workbook metadata, free text, money, or tenant identifiers leave
this module. Output is an unvalidated observation, never underwriting approval.
"""
from __future__ import annotations

import hashlib
import io
import re
import warnings
from typing import Any

from .pms_normalizer import RentRollNormalizationError

__all__ = [
    "BASE_RENT_CODES",
    "ANCILLARY_CATEGORIES",
    "ANCILLARY_CODES",
    "classify_charge",
    "is_base_rent",
    "is_ancillary_charge",
    "normalize_pdf_rent_roll",
]

_MAX_BYTES = 32 * 1024 * 1024
_MAX_PAGES = 500
_MAX_LINES = 50_000
_MAX_CELLS = 250_000

_COUNTS = ("occupied", "vacant", "down", "total")
_SCOPES = ("residential", "commercial")

_CLOSED_CODES = frozenset({
    "MALFORMED_INPUT",
    "INPUT_LIMIT_EXCEEDED",
    "UNSUPPORTED_PDF_LAYOUT",
    "EMPTY_INPUT",
    "PDF_DEPENDENCY_MISSING",
})

# Explicit use labels only: floorplans (e.g. 1BR) do not establish asset use.
_TYPES = {
    "residential": "residential",
    "apartment": "residential",
    "commercial": "commercial",
    "retail": "commercial",
    "office": "commercial",
}

BASE_RENT_CODES = frozenset({
    "rent",
    "base",
    "base rent",
    "baserent",
    "mrent",
    "market rent",
    "apt rent",
    "aptr",
    "lease rent",
    "resrent",
    "contract rent",
    "actual rent",
    "resident rent",
})

_NON_CHARGE_HEADERS = frozenset({
    "lease id", "lease start", "lease end", "lease term",
    "move in", "move out", "balance", "deposit", "expiration",
})

ANCILLARY_CATEGORIES: dict[str, frozenset[str]] = {
    "parking": frozenset({
        "park", "parking", "garage", "carport", "space", "lot",
        "covered parking", "reserved parking",
    }),
    "pet": frozenset({
        "pet", "pet rent", "petrent", "pet fee", "animal", "dog", "cat",
    }),
    "storage": frozenset({
        "stor", "storage", "locker", "storage locker",
    }),
    "utilities": frozenset({
        "util", "utility", "water", "sewer", "trash", "electric", "gas",
        "rubs", "cable", "tech", "valet trash", "pest",
    }),
    "washer_dryer": frozenset({
        "washer", "dryer", "w/d", "wd", "w d", "laundry", "appliance",
    }),
    "concessions": frozenset({
        "conc", "concession", "discount", "free rent", "credit",
    }),
}

ANCILLARY_CODES = frozenset.union(*ANCILLARY_CATEGORIES.values())

_DATE_RE = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})$")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


class _Failure(Exception):
    """Internal static failure code wrapper."""


def _key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"[\s/,:\-_]+", " ", value.strip().lower()).strip()
    return re.sub(r"[\s/,:\-_]+", " ", str(value).strip().lower()).strip()


def classify_charge(code: str | None) -> str:
    """Classify charge code into 'base_rent', specific ancillary category, or 'unknown'."""
    if not code:
        return "unknown"
    k = _key(code)
    if not k or k in _NON_CHARGE_HEADERS:
        return "unknown"
    for cat, aliases in ANCILLARY_CATEGORIES.items():
        if k in aliases or any(k == a or k.startswith(a + " ") or k.endswith(" " + a) for a in aliases):
            return cat
    if k in BASE_RENT_CODES or any(
        (" " in b) and (k.startswith(b + " ") or k.endswith(" " + b))
        for b in BASE_RENT_CODES
    ):
        return "base_rent"
    return "unknown"


def is_base_rent(code: str | None) -> bool:
    return classify_charge(code) == "base_rent"


def is_ancillary_charge(code: str | None) -> bool:
    return classify_charge(code) in ANCILLARY_CATEGORIES


def _map_status(name_str: str) -> str | None:
    key = _key(name_str)
    if not key:
        return "vacant"
    if any(key == m or key.startswith(m + " ") for m in ("model", "admin")):
        return None
    if any(key == v or key.startswith(v + " ") for v in ("vacant", "unrented", "vac")):
        return "vacant"
    if any(key == d or key.startswith(d + " ") for d in ("down", "offline", "out of service")):
        return "down"
    if any(key == o or key.startswith(o + " ") for o in ("occupied", "current", "notice")):
        return "occupied"
    return "occupied"


def _is_positive_num(val: str) -> bool:
    try:
        clean = val.replace(",", "").strip()
        return float(clean) > 0.0
    except (ValueError, TypeError):
        return False


def _is_line_a(text: str) -> bool:
    k = _key(text)
    required = ("unit", "unit type", "market", "balance")
    return (
        all(r in k for r in required)
        and ("name" in k or "resident" in k)
        and ("lease" in k or "move out" in k)
    )


def _is_line_b(text: str) -> bool:
    k = _key(text)
    required = ("sq ft", "rent", "expiration")
    return all(r in k for r in required) or ("sq ft" in k and "deposit" in k and "rent" in k)


def _is_current_section(text: str) -> bool:
    k = _key(text)
    return any(
        s in k
        for s in (
            "current notice vacant residents",
            "current residents",
            "current units",
            "current tenants",
        )
    )


def _is_non_current_section(text: str) -> bool:
    k = _key(text)
    return any(
        s in k
        for s in (
            "future residents",
            "future residents/applicants",
            "applicants",
            "former residents",
            "future tenants",
            "pending renewals",
        )
    )


def _is_totals(text: str) -> bool:
    k = _key(text)
    return any(
        s in k
        for s in (
            "totals:",
            "totals",
            "total east quarter",
            "total 150 summit",
            "grand total",
            "report total",
            "summary groups",
            "summary",
        )
    )


def _parse_unit_line(line: str) -> dict[str, Any] | None:
    tokens = line.strip().split()
    if len(tokens) < 4:
        return None
    unit_id = tokens[0]
    unit_type = tokens[1]
    if not _NUM_RE.match(tokens[2]):
        return None
    sq_ft = tokens[2]

    idx = len(tokens) - 1
    if not _NUM_RE.match(tokens[idx]):
        return None
    balance = tokens[idx]
    idx -= 1

    dates: list[str] = []
    while idx >= 3 and _DATE_RE.match(tokens[idx]):
        dates.append(tokens[idx])
        idx -= 1
    dates.reverse()

    amounts: list[str] = []
    while idx >= 3 and _NUM_RE.match(tokens[idx]):
        amounts.append(tokens[idx])
        idx -= 1
    amounts.reverse()

    if idx < 2:
        return None

    resident_name = " ".join(tokens[3 : idx + 1])
    resident_rent = amounts[1] if len(amounts) >= 2 else (amounts[0] if amounts else "0.00")

    return {
        "unit_id": unit_id,
        "unit_type": unit_type,
        "sq_ft": sq_ft,
        "resident_name": resident_name,
        "resident_rent": resident_rent,
        "amounts": amounts,
        "dates": dates,
        "balance": balance,
    }


def _parse_pdf(raw_bytes: bytes) -> dict[str, Any]:
    try:
        import pdfplumber
        import pdfminer  # noqa: F401
    except ImportError:
        raise _Failure("PDF_DEPENDENCY_MISSING") from None

    try:
        pdf = pdfplumber.open(io.BytesIO(raw_bytes))
    except Exception:
        raise _Failure("MALFORMED_INPUT") from None

    try:
        if len(pdf.pages) > _MAX_PAGES:
            raise _Failure("INPUT_LIMIT_EXCEEDED")
        if len(pdf.pages) < 2:
            raise _Failure("UNSUPPORTED_PDF_LAYOUT")

        digest = hashlib.sha256(raw_bytes).hexdigest()

        # Bounds checks on lines and cells
        total_lines = 0
        pages_lines: list[list[str]] = []
        for page in pdf.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                raise _Failure("MALFORMED_INPUT") from None
            lines = text.splitlines()
            total_lines += len(lines)
            if total_lines > _MAX_LINES:
                raise _Failure("INPUT_LIMIT_EXCEEDED")
            if total_lines * 11 > _MAX_CELLS:
                raise _Failure("INPUT_LIMIT_EXCEEDED")
            pages_lines.append(lines)

        # Validate Page 1 Layout Signature
        p1_lines = pages_lines[0]
        if not p1_lines:
            raise _Failure("UNSUPPORTED_PDF_LAYOUT")

        title_found = any("rent roll" in _key(l) for l in p1_lines[:5])
        if not title_found:
            raise _Failure("UNSUPPORTED_PDF_LAYOUT")

        line_a_idx: int | None = None
        line_b_idx: int | None = None
        for i, l in enumerate(p1_lines[:15]):
            if _is_line_a(l):
                line_a_idx = i
                break

        if line_a_idx is not None and line_a_idx + 1 < len(p1_lines):
            if _is_line_b(p1_lines[line_a_idx + 1]):
                line_b_idx = line_a_idx + 1

        if line_a_idx is None or line_b_idx is None:
            raise _Failure("UNSUPPORTED_PDF_LAYOUT")

        section_found = False
        for l in p1_lines[line_b_idx + 1 : line_b_idx + 6]:
            if _is_current_section(l):
                section_found = True
                break

        if not section_found:
            raise _Failure("UNSUPPORTED_PDF_LAYOUT")

        result: dict[str, Any] = {
            "version": "1.0",
            "pms_type": "realpage",
            "source_sha256": digest,
            "residential_units": [],
            "commercial_units": [],
            "counts": {s: dict.fromkeys(_COUNTS) for s in _SCOPES},
            "summary": {
                s: {"status": "absent", "reported_counts": None, "citations": []}
                for s in _SCOPES
            },
            "issues": [],
            "status": "normalized_unvalidated",
        }

        def cite(page_idx: int, line_idx: int, col_idx: int) -> dict[str, Any]:
            return {
                "source_sha256": digest,
                "sheet": page_idx,
                "row": line_idx,
                "row_end": line_idx,
                "column": col_idx,
            }

        def issue(
            code: str,
            c: dict[str, Any] | None = None,
            *,
            warning: bool = False,
            observation: dict[str, Any] | None = None,
        ) -> None:
            entry: dict[str, Any] = {
                "code": code,
                "severity": "warning" if warning else "blocker",
                "citation": c,
            }
            if observation is not None:
                entry["observation"] = observation
            result["issues"].append(entry)
            if not warning:
                result["status"] = "blocked"

        units: dict[str, dict[str, Any]] = {}
        conflicts: set[str] = set()
        current_unit: dict[str, Any] | None = None
        current_unit_id: str | None = None
        inventory_valid = True
        in_current_section = False
        in_excluded_section = False
        parsing_terminated = False

        for page_idx, lines in enumerate(pages_lines, 1):
            if parsing_terminated:
                break
            for line_idx, raw_line in enumerate(lines, 1):
                if parsing_terminated:
                    break
                l_str = raw_line.strip()
                if not l_str:
                    continue

                if _is_totals(l_str):
                    parsing_terminated = True
                    break

                if _is_non_current_section(l_str):
                    in_current_section = False
                    in_excluded_section = True
                    issue("NON_CURRENT_SECTION_EXCLUDED", cite(page_idx, line_idx, 1), warning=True)
                    continue

                if _is_current_section(l_str):
                    in_current_section = True
                    in_excluded_section = False
                    continue

                # Skip header band on repeated pages
                if _is_line_a(l_str) or _is_line_b(l_str):
                    continue
                k_line = _key(l_str)
                if (
                    "rent roll" in k_line
                    or "as of =" in k_line
                    or "month year =" in k_line
                ):
                    continue

                if in_excluded_section or not in_current_section:
                    continue

                parsed = _parse_unit_line(l_str)
                if parsed is None:
                    # Check if continuation line (e.g. ancillary fee)
                    tokens = l_str.split()
                    if (
                        len(tokens) >= 2
                        and is_ancillary_charge(tokens[0])
                        and _NUM_RE.match(tokens[1])
                    ):
                        if current_unit is None:
                            inventory_valid = False
                            issue("ORPHAN_CONTINUATION", cite(page_idx, line_idx, 1))
                        else:
                            current_unit["evidence"].append({"charge": cite(page_idx, line_idx, 6)})
                            issue("CONTINUATION_EVIDENCE", cite(page_idx, line_idx, 6), warning=True)
                    continue

                unit_id = parsed["unit_id"]
                type_str = parsed["unit_type"]
                name_str = parsed["resident_name"]
                rent_str = parsed["resident_rent"]

                unit_status = _map_status(name_str)
                if unit_status is None:
                    inventory_valid = False
                    issue("UNSUPPORTED_STATUS", cite(page_idx, line_idx, 4))
                    continue

                resolved_use = _TYPES.get(_key(type_str))

                evidence_primary: dict[str, Any] = {
                    "unit_id": cite(page_idx, line_idx, 1),
                    "status": cite(page_idx, line_idx, 4),
                }
                if type_str:
                    evidence_primary["unit_type"] = cite(page_idx, line_idx, 2)
                if name_str and unit_status == "occupied":
                    evidence_primary["tenant_name"] = cite(page_idx, line_idx, 4)
                if rent_str and _is_positive_num(rent_str):
                    evidence_primary["charge"] = cite(page_idx, line_idx, 6)

                if unit_id in conflicts:
                    issue("DUPLICATE_UNIT_CONFLICT", cite(page_idx, line_idx, 1))
                    current_unit = None
                    current_unit_id = None
                    continue

                existing = units.get(unit_id)
                if existing:
                    if existing["status"] != unit_status:
                        inventory_valid = False
                        conflicts.add(unit_id)
                        existing["status"] = None
                        issue("DUPLICATE_UNIT_CONFLICT", cite(page_idx, line_idx, 1))
                    else:
                        issue("DUPLICATE_UNIT_EVIDENCE", cite(page_idx, line_idx, 1), warning=True)
                    existing["evidence"].append(evidence_primary)
                    current_unit = existing
                    current_unit_id = unit_id
                else:
                    current_unit = {
                        "unit_id": unit_id,
                        "status": unit_status,
                        "unit_type": resolved_use,
                        "tenant_name": "[REDACTED]",
                        "evidence": [evidence_primary],
                    }
                    units[unit_id] = current_unit
                    current_unit_id = unit_id

        if not units:
            inventory_valid = False
            issue("NO_CURRENT_UNITS", cite(1, 1, 1))

        has_unresolved = False
        for u in units.values():
            if u["unit_type"] is None:
                has_unresolved = True
                u_row = u["evidence"][0]["unit_id"]["row"]
                u_sheet = u["evidence"][0]["unit_id"]["sheet"]
                issue("UNRESOLVED_UNIT_USE", cite(u_sheet, u_row, 2), observation=u)
            elif u["unit_type"] == "residential":
                result["residential_units"].append(u)
            elif u["unit_type"] == "commercial":
                result["commercial_units"].append(u)

        if has_unresolved or not inventory_valid:
            result["status"] = "blocked"
            for scope in _SCOPES:
                result["counts"][scope] = dict.fromkeys(_COUNTS)
        else:
            for scope in _SCOPES:
                scope_units = result[f"{scope}_units"]
                derived = {
                    "occupied": sum(u["status"] == "occupied" for u in scope_units),
                    "vacant": sum(u["status"] == "vacant" for u in scope_units),
                    "down": sum(u["status"] == "down" for u in scope_units),
                    "total": len(scope_units),
                }
                result["counts"][scope] = derived

        return result

    finally:
        pdf.close()


def normalize_pdf_rent_roll(raw_bytes: bytes) -> dict[str, Any]:
    """Parse and normalize Summit / East Quarter PDF rent roll into v1 observation dict.

    Never executes external commands, links, or JavaScript.
    Tenant identities are strictly redacted.
    Structural failures raise sanitized RentRollNormalizationError with closed error codes.
    """
    code: str = "MALFORMED_INPUT"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            if not isinstance(raw_bytes, bytes):
                raise _Failure("MALFORMED_INPUT")
            if len(raw_bytes) > _MAX_BYTES:
                raise _Failure("INPUT_LIMIT_EXCEEDED")
            if not raw_bytes or not raw_bytes.strip():
                raise _Failure("EMPTY_INPUT")
            if not raw_bytes.startswith(b"%PDF-"):
                raise _Failure("MALFORMED_INPUT")
            return _parse_pdf(raw_bytes)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = "MALFORMED_INPUT"

    if code not in _CLOSED_CODES:
        code = "MALFORMED_INPUT"
    raise RentRollNormalizationError(code) from None
