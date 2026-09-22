"""Deterministic local slicer for financial sections of offering memoranda.

Read-only selection of candidate financial pages from a multi-page OM PDF:
operating statements, T12s, rent rolls, broker tax assumptions, debt
structures and lease terms. Output preserves original page bounds and local
text lines with 1-indexed grid citations; every figure remains an unverified
broker claim. No cloud calls, no OCR, no numeric synthesis: pages without an
extractable text layer are reported OCR-required rather than invented, and a
fixed page-count budget never silently cuts financial context — overflow
surfaces as a needs-review blocker. Structural failures raise sanitized
RentRollNormalizationError with closed error codes.
"""
from __future__ import annotations

import copy
import hashlib
import io
import re
import warnings
from typing import Any

from .pms_normalizer import RentRollNormalizationError

__all__ = [
    "DEFAULT_PAGE_BUDGET",
    "MAX_BYTES",
    "MAX_PAGES",
    "MAX_TOTAL_CHARS",
    "FINANCIAL_SECTION_CODES",
    "financial_section_codes",
    "slice_om_sections",
]

_MAX_BYTES = 32 * 1024 * 1024
MAX_PAGES = 500
MAX_TOTAL_CHARS = 2_000_000
DEFAULT_PAGE_BUDGET = 40

_CLOSED_CODES = frozenset({
    "MALFORMED_INPUT",
    "INPUT_LIMIT_EXCEEDED",
    "EMPTY_INPUT",
    "PDF_DEPENDENCY_MISSING",
    "EXTRACTION_FAILURE",
})

# Ordered registry: first matching section code wins. Tax assumptions are
# listed before generic statement aliases so broker tax prose never lands in
# operating statements; aliases are mutable sets so registry copies survive
# caller mutation probes without leaking back into this module.
FINANCIAL_SECTION_CODES: dict[str, dict[str, Any]] = {
    "tax_assumptions": {
        "aliases": {
            "tax assumption",
            "tax assumptions",
            "tax assumptions and projections",
            "broker tax assumptions",
        },
    },
    "operating_statements": {
        "aliases": {
            "operating statement",
            "operating statements",
            "trailing twelve month operating statement",
            "trailing twelve operating statement",
            "profit and loss statement",
            "p&l statement",
            "income and expense statement",
            "income and expense statements",
        },
    },
    "t12": {
        "aliases": {
            "t12",
            "t 12",
            "t.12",
            "trailing twelve",
            "trailing 12",
            "twelve month operating statement",
        },
    },
    "rent_roll": {
        "aliases": {
            "rent roll summary",
            "rent roll",
            "rentroll",
        },
    },
    "debt_structure": {
        "aliases": {
            "debt structure",
            "financing assumptions",
            "loan summary",
            "current debt",
        },
    },
    "lease_terms": {
        "aliases": {
            "lease summary",
            "lease terms",
            "lease abstracts",
            "rent schedule",
            "commercial lease summary",
        },
    },
}

_TOC_TOKENS = ("table of contents", "contents")
_TOC_LEADER_RE = re.compile(r"\.{3,}|\u2026")
_TOC_MARKER_WORDS = frozenset({"overview", "summary", "introduction", "appendix"})


class _Failure(Exception):
    """Internal static failure code wrapper."""


def _key(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s/,:\-_\u2026.]+", " ", str(value).strip().lower()).strip()


def financial_section_codes() -> dict[str, dict[str, Any]]:
    """Deep copy of the section registry; caller mutations never leak back."""
    return copy.deepcopy(FINANCIAL_SECTION_CODES)


def _match_section_heading(keyed_line: str) -> tuple[str, str] | None:
    """Return (code, matched alias) for a heading line, or None."""
    for code, spec in FINANCIAL_SECTION_CODES.items():
        for alias in spec["aliases"]:
            if keyed_line == alias or keyed_line.startswith(alias + " "):
                return code, alias
    return None


def _is_toc_page(raw_lines: list[str]) -> bool:
    """Detect a table-of-contents page by title plus dotted leader entries."""
    stripped = [l.strip() for l in raw_lines if l and l.strip()]
    if not stripped:
        return False
    head = " ".join(stripped[:12]).lower()
    if not any(tok in head for tok in _TOC_TOKENS):
        return False
    dotted = [l for l in stripped if _TOC_LEADER_RE.search(l)]
    if not dotted:
        return False
    # require at least two dotted entries or a dotted entry naming a marker word
    if len(dotted) >= 2:
        return True
    return any(any(w in l.lower() for w in _TOC_MARKER_WORDS) for l in dotted)


def _cite(digest: str, page_idx: int, line_idx: int, col_idx: int = 1) -> dict[str, Any]:
    return {
        "source_sha256": digest,
        "sheet": page_idx,
        "row": line_idx,
        "row_end": line_idx,
        "column": col_idx,
    }


def _extract_pages(raw_bytes: bytes) -> list[list[str]]:
    """Extract per-page text lines; raise _Failure on structural problems."""
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
        if len(pdf.pages) > MAX_PAGES:
            raise _Failure("INPUT_LIMIT_EXCEEDED")
        pages_lines: list[list[str]] = []
        total_chars = 0
        for page in pdf.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                raise _Failure("EXTRACTION_FAILURE") from None
            if not text.strip():
                pages_lines.append([])
                continue
            total_chars += len(text)
            if total_chars > MAX_TOTAL_CHARS:
                raise _Failure("INPUT_LIMIT_EXCEEDED")
            pages_lines.append(text.splitlines())
        return pages_lines
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _slice(raw_bytes: bytes, toc_pages: set[int], page_budget: int) -> dict[str, Any]:
    pages_lines = _extract_pages(raw_bytes)
    total_pages = len(pages_lines)
    digest = hashlib.sha256(raw_bytes).hexdigest()

    auto_toc = {i for i, lines in enumerate(pages_lines, 1) if _is_toc_page(lines)}
    toc_set = auto_toc | toc_pages

    # Heading pass: first matching heading line per non-TOC page.
    ordered: list[tuple[int, str, int, str]] = []  # (page_idx, code, line_idx, alias)
    for page_idx, lines in enumerate(pages_lines, 1):
        if page_idx in toc_set:
            continue
        for line_idx, line in enumerate(lines, 1):
            keyed_line = _key(line)
            if not keyed_line:
                continue
            match = _match_section_heading(keyed_line)
            if match is not None:
                ordered.append((page_idx, match[0], line_idx, match[1]))
                break

    if not ordered:
        return {
            "version": "1.0",
            "source_sha256": digest,
            "total_pages": total_pages,
            "sections": [],
            "selected_pages": [],
            "ocr_required_pages": [],
            "status": "blocked",
            "issues": [{
                "code": "NO_FINANCIAL_SECTIONS",
                "severity": "blocker",
                "citation": _cite(digest, 1, 1),
            }],
        }

    # Section bounds: a same-code heading on the next heading page extends the
    # section (repeated bands never duplicate and never terminate); a
    # different-code heading page terminates the previous section's span.
    # Computed right-to-left so chains of same-code headings collapse.
    ends: list[int] = [0] * len(ordered)
    for i in range(len(ordered) - 1, -1, -1):
        if i + 1 < len(ordered) and ordered[i + 1][1] == ordered[i][1]:
            ends[i] = ends[i + 1]
        elif i + 1 < len(ordered):
            ends[i] = ordered[i + 1][0] - 1
        else:
            ends[i] = total_pages
        ends[i] = max(ends[i], ordered[i][0])

    sections: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    for (page_idx, code, line_idx, alias), end in zip(ordered, ends):
        if code in by_code:
            section = by_code[code]
            section["page_end"] = max(section["page_end"], end)
            continue  # repeated heading extends, never duplicates
        section = {
            "code": code,
            "heading_text": pages_lines[page_idx - 1][line_idx - 1].strip(),
            "heading_citation": _cite(digest, page_idx, line_idx),
            "page_start": page_idx,
            "page_end": end,
            "text_lines": [],
            "attribution": "broker_claim",
            "provenance": "unverified_broker_provided",
        }
        by_code[code] = section
        sections.append(section)

    candidate_pages: set[int] = set()
    for section in sections:
        candidate_pages.update(range(section["page_start"], section["page_end"] + 1))
    candidate_sorted = sorted(candidate_pages)

    # OCR-required pages: no extractable text layer on a non-TOC page.
    ocr_pages = [
        i for i, lines in enumerate(pages_lines, 1)
        if not lines and i not in toc_set
    ]
    issues: list[dict[str, Any]] = [{
        "code": "OCR_REQUIRED",
        "severity": "warning",
        "citation": _cite(digest, i, 1),
    } for i in ocr_pages]

    selected = candidate_sorted[:page_budget]
    if len(candidate_sorted) > page_budget:
        issues.append({
            "code": "PAGE_BUDGET_OVERFLOW",
            "severity": "blocker",
            "citation": None,
            "total_candidate_pages": len(candidate_sorted),
            "overflow_pages": len(candidate_sorted) - len(selected),
        })

    if len(candidate_sorted) > page_budget:
        status = "needs_review"
    elif ocr_pages:
        status = "incomplete"
    else:
        status = "ok"

    selected_set = set(selected)
    for section in sections:
        section["text_lines"] = [
            line
            for p in range(section["page_start"], section["page_end"] + 1)
            if p in selected_set
            for line in pages_lines[p - 1]
        ]

    return {
        "version": "1.0",
        "source_sha256": digest,
        "total_pages": total_pages,
        "sections": sections,
        "selected_pages": selected,
        "ocr_required_pages": ocr_pages,
        "status": status,
        "issues": issues,
    }


def slice_om_sections(
    raw_bytes: bytes,
    *,
    toc_pages: set[int] | None = None,
    page_budget: int | None = None,
) -> dict[str, Any]:
    """Slice candidate financial sections from an OM PDF.

    Deterministic, local-only page selection with original bounds and grid
    citations. Broker figures remain broker claims; scanned pages surface as
    OCR-required; budget overflow is reported, never silently cut.
    Structural failures raise sanitized RentRollNormalizationError.
    """
    code: str = "MALFORMED_INPUT"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            if not isinstance(raw_bytes, (bytes, bytearray)):
                raise _Failure("MALFORMED_INPUT")
            raw = bytes(raw_bytes)
            if len(raw) > _MAX_BYTES:
                raise _Failure("INPUT_LIMIT_EXCEEDED")
            if not raw or not raw.strip():
                raise _Failure("EMPTY_INPUT")
            if not raw.startswith(b"%PDF-"):
                raise _Failure("MALFORMED_INPUT")
            if toc_pages is None:
                toc = set()
            else:
                toc = {int(p) for p in toc_pages}
                if any(p < 1 for p in toc):
                    raise _Failure("MALFORMED_INPUT")
            budget = DEFAULT_PAGE_BUDGET if page_budget is None else int(page_budget)
            if budget < 1:
                raise _Failure("MALFORMED_INPUT")
            return copy.deepcopy(_slice(raw, toc, budget))
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = "MALFORMED_INPUT"

    if code not in _CLOSED_CODES:
        code = "MALFORMED_INPUT"
    raise RentRollNormalizationError(code) from None
