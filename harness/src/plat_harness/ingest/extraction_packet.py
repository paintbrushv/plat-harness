"""Egress-safe extraction packet builder for OM financial context.

Converts a slicer result (or slicer-shaped dict) into the single structure
permitted to leave the host: a rebuilt, allowlisted, redacted packet. Source
text is untrusted data, never authority — it cannot self-authorize egress,
grant approvals, or inject tool instructions. Typed PII (emails, phones,
SSN-shaped tokens, labeled person names) is replaced with typed placeholders
in rebuilt payloads; prose that cannot be safely classified (addresses,
contact prose) stays local-only with a citation. Over-budget lines are
excluded whole — never cut mid-line, which would silently lose trailing
footnote scope — and every omitted line is accounted for with a citation.
Failures raise sanitized RentRollNormalizationError with closed error codes.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .pms_normalizer import RentRollNormalizationError

__all__ = [
    "MAX_LINE_CHARS",
    "MAX_METADATA_CHARS",
    "MAX_PACKET_CHARS",
    "MAX_SECTIONS",
    "METADATA_ALLOWLIST",
    "build_extraction_packet",
    "egress_payload",
]

MAX_LINE_CHARS = 2_000
MAX_PACKET_CHARS = 200_000
MAX_METADATA_CHARS = 200
MAX_SECTIONS = 500
REDACTION_RECORD_LIMIT = 200
LOCAL_ONLY_RECORD_LIMIT = 1_000

# Document metadata allowlist: author-free, host-safe keys only. Everything
# else (author, custom keys, thumbnails, attachments) is dropped whole.
METADATA_ALLOWLIST = frozenset({
    "title",
    "subject",
    "producer",
    "creator",
    "pages",
})

_CLOSED_CODES = frozenset({
    "MALFORMED_INPUT",
    "INPUT_LIMIT_EXCEEDED",
    "EGRESS_NOT_APPROVED",
    "PACKET_NOT_READY",
    "REDACTION_FAILURE",
})

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(
    r"(?<![\d,.])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?![\d])"
)
_SSN_RE = re.compile(r"(?<![\d])\d{3}-\d{2}-\d{4}(?![\d])")
_PERSON_LABEL_RE = re.compile(
    r"\b(?:resident|tenant|lessee|occupant|applicant)\s*[:\-]\s*"
    r"(?:[A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+){0,3})",
    re.IGNORECASE,
)
_INJECTION_RES = (
    re.compile(
        r"\b(?:ignore|disregard)\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+"
        r"instructions\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bapprove\s+the\s+(?:following|deal)\b", re.IGNORECASE),
)
# A digit token possibly grouped by thousands/decimal separators.
_DIGIT_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

_REDACTED = "[REDACTED"


def _placeholder(code: str) -> str:
    return _REDACTED + ":" + code + "]"


class _Failure(Exception):
    """Internal static failure code wrapper."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _redact_line(text: str) -> tuple[str | None, str | None]:
    """Redact one source line.

    Returns (redacted_text, reason) when the line can safely leave the host,
    or (None, reason) when it must stay local-only. Typed PII is replaced
    with typed placeholders; unclassifiable digit-bearing prose (addresses,
    contact blocks) is never guessed at — it stays local-only.
    """
    for pattern in _INJECTION_RES:
        if pattern.search(text):
            return None, "PROMPT_INJECTION_SUSPECT"

    redacted, reason = text, None

    def _sub(pattern: re.Pattern, placeholder: str, code: str) -> None:
        nonlocal redacted, reason
        if pattern.search(redacted):
            redacted = pattern.sub(placeholder, redacted)
            reason = reason or code

    _sub(_EMAIL_RE, _placeholder("email"), "email")
    _sub(_PHONE_RE, _placeholder("phone"), "phone")
    _sub(_SSN_RE, _placeholder("pii"), "pii")
    if _PERSON_LABEL_RE.search(redacted):
        redacted = _PERSON_LABEL_RE.sub(
            lambda m: m.group(0).split(":", 1)[0] + ": " + _placeholder("person_name"),
            redacted,
        )
        reason = reason or "person_name"

    # Ungrouped 3+ digit runs that are not financial figures or years look
    # like addresses/contact prose: they cannot be token-redacted safely.
    for token in _DIGIT_TOKEN_RE.findall(redacted):
        if "," in token or "." in token:
            continue  # grouped financial figure (e.g. 1,000,000 / 25.31)
        if len(token) <= 2:
            continue
        if _YEAR_RE.fullmatch(token):
            continue
        return None, "UNSAFE_PII_LOCAL_ONLY"
    return redacted, reason


def _redact_value(text: str) -> str:
    """Typed-only redaction for allowlisted structured cells/metadata."""
    redacted = _EMAIL_RE.sub(_placeholder("email"), text)
    redacted = _PHONE_RE.sub(_placeholder("phone"), redacted)
    redacted = _SSN_RE.sub(_placeholder("pii"), redacted)
    redacted = _PERSON_LABEL_RE.sub(
        lambda m: m.group(0).split(":", 1)[0] + ": " + _placeholder("person_name"),
        redacted,
    )
    return redacted


def _contains_pii(text: str) -> bool:
    """Fail-closed self-check: any typed PII surviving into a kept payload."""
    return bool(
        _EMAIL_RE.search(text)
        or _PHONE_RE.search(text)
        or _SSN_RE.search(text)
        or _PERSON_LABEL_RE.search(text)
    )


def _cite(digest: str, page: int, line_idx: int) -> dict[str, Any]:
    return {
        "source_sha256": digest,
        "sheet": page,
        "row": line_idx,
        "row_end": line_idx,
        "column": 1,
    }


def _require(condition: Any, code: str = "MALFORMED_INPUT") -> None:
    if not condition:
        raise _Failure(code)


def _validate_section(section: Any, total_pages: int) -> None:
    _require(isinstance(section, dict))
    _require(isinstance(section.get("code"), str) and bool(section["code"]))
    page_start = section.get("page_start")
    page_end = section.get("page_end")
    _require(_is_int(page_start) and page_start >= 1)
    _require(_is_int(page_end) and page_end >= page_start and page_end <= total_pages)
    heading = section.get("heading_citation")
    _require(isinstance(heading, dict))
    _require({"source_sha256", "sheet", "row"}.issubset(heading.keys()))
    lines = section.get("text_lines")
    _require(isinstance(lines, list))
    _require(all(isinstance(l, str) for l in lines))
    rows = section.get("rows", [])
    _require(isinstance(rows, list))
    for row in rows:
        _require(isinstance(row, dict))
        for key, value in row.items():
            _require(isinstance(key, str))
            _require(value is None or isinstance(value, (str, int)))


def _build(
    sliced: dict[str, Any],
    *,
    egress_approved: bool,
    document_metadata: dict[str, Any] | None,
    allowed_columns: frozenset[str] | None,
) -> dict[str, Any]:
    digest = sliced["source_sha256"]
    total_pages = sliced["total_pages"]
    sections = sliced["sections"]

    packet_sections: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    redactions: list[dict[str, Any]] = []
    local_only: list[dict[str, Any]] = []
    redactions_truncated = False
    local_truncated = False
    packet_overflow = False
    kept_chars = 0
    kept_any = False

    for section in sections:
        lines_out: list[dict[str, Any]] = []
        for idx, raw_line in enumerate(section["text_lines"], 1):
            if len(raw_line) > MAX_LINE_CHARS:
                # Never cut mid-line: an over-budget line is excluded whole
                # (cutting would silently lose trailing footnote scope).
                entry = {
                    "reason_code": "LINE_BUDGET_EXCEEDED",
                    "line_index": idx,
                    "citation": _cite(digest, section["page_start"], idx),
                }
                if len(local_only) < LOCAL_ONLY_RECORD_LIMIT:
                    local_only.append(entry)
                else:
                    local_truncated = True
                continue
            redacted, reason = _redact_line(raw_line)
            if redacted is None:
                entry = {
                    "reason_code": reason,
                    "line_index": idx,
                    "citation": _cite(digest, section["page_start"], idx),
                }
                if len(local_only) < LOCAL_ONLY_RECORD_LIMIT:
                    local_only.append(entry)
                else:
                    local_truncated = True
                continue
            if kept_chars + len(redacted) > MAX_PACKET_CHARS:
                packet_overflow = True
                entry = {
                    "reason_code": "PACKET_BUDGET_EXCEEDED",
                    "line_index": idx,
                    "citation": _cite(digest, section["page_start"], idx),
                }
                if len(local_only) < LOCAL_ONLY_RECORD_LIMIT:
                    local_only.append(entry)
                else:
                    local_truncated = True
                continue
            if _contains_pii(redacted):
                # Fail closed: the redactor leaked; never ship the payload.
                raise _Failure("REDACTION_FAILURE")
            if reason is not None and len(redactions) < REDACTION_RECORD_LIMIT:
                redactions.append({
                    "reason_code": reason,
                    "citation": _cite(digest, section["page_start"], idx),
                })
            elif reason is not None:
                redactions_truncated = True
            lines_out.append({
                "text": redacted,
                "line_index": idx,
                "citation": _cite(digest, section["page_start"], idx),
            })
            kept_chars += len(redacted)
            kept_any = True

        rows_out: list[dict[str, Any]] = []
        input_rows = section.get("rows", [])
        if input_rows and allowed_columns is None:
            issues.append({
                "code": "STRUCTURED_COLUMNS_UNAPPROVED",
                "severity": "warning",
                "citation": None,
            })
        elif input_rows:
            for ridx, row in enumerate(input_rows, 1):
                cells: dict[str, Any] = {}
                for key, value in row.items():
                    if key not in allowed_columns:
                        continue  # unknown/unapproved columns are dropped
                    cell = "" if value is None else _redact_value(str(value))
                    if _contains_pii(cell):
                        raise _Failure("REDACTION_FAILURE")
                    cells[key] = cell
                rows_out.append({
                    "row_index": ridx,
                    "cells": cells,
                    "citation": _cite(digest, section["page_start"], ridx),
                })
                kept_any = True

        packet_sections.append({
            "code": section["code"],
            "page_start": section["page_start"],
            "page_end": section["page_end"],
            "attribution": section.get("attribution", "broker_claim"),
            "lines": lines_out,
            "rows": rows_out,
        })

    # OCR-required pages pass through with warnings; they are never invented.
    ocr_pages = list(sliced.get("ocr_required_pages", []))
    for page in ocr_pages:
        issues.append({
            "code": "OCR_REQUIRED",
            "severity": "warning",
            "citation": _cite(digest, page, 1),
        })

    if packet_overflow:
        omitted = sum(
            1 for e in local_only if e["reason_code"] == "PACKET_BUDGET_EXCEEDED"
        )
        issues.append({
            "code": "PACKET_BUDGET_OVERFLOW",
            "severity": "blocker",
            "citation": None,
            "omitted_lines": omitted,
        })
    # Every local-only reason surfaces as a visible warning; nothing silently
    # disappears from the egress view.
    for reason in ("UNSAFE_PII_LOCAL_ONLY", "PROMPT_INJECTION_SUSPECT",
                   "LINE_BUDGET_EXCEEDED"):
        if any(e["reason_code"] == reason for e in local_only):
            issues.append({
                "code": reason,
                "severity": "warning",
                "citation": None,
            })
    if redactions_truncated:
        issues.append({
            "code": "REDACTION_RECORDS_TRUNCATED",
            "severity": "warning",
            "citation": None,
        })
    if local_truncated:
        issues.append({
            "code": "LOCAL_ONLY_RECORDS_TRUNCATED",
            "severity": "warning",
            "citation": None,
        })

    # Allowlisted, redacted document metadata only.
    metadata: dict[str, Any] = {}
    if document_metadata:
        for key in sorted(document_metadata.keys()):
            if key not in METADATA_ALLOWLIST:
                continue  # author, custom keys and anything unknown: dropped
            value = document_metadata[key]
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                redacted = _redact_value(str(value))
                if len(redacted) > MAX_METADATA_CHARS:
                    continue
                if _contains_pii(redacted):
                    raise _Failure("REDACTION_FAILURE")
                match = _DIGIT_TOKEN_RE.search(redacted)
                if match is not None:
                    token = match.group(0)
                    unsafe = (
                        "," not in token
                        and "." not in token
                        and len(token) > 2
                        and not _YEAR_RE.fullmatch(token)
                    )
                    if unsafe:
                        continue  # unclassifiable metadata value stays local
                if len(redactions) < REDACTION_RECORD_LIMIT:
                    redactions.append({
                        "reason_code": "metadata_redaction",
                        "citation": None,
                    }) if redacted != str(value) else None
                metadata[key] = int(value) if _is_int(value) else redacted

    # Status honesty.
    has_content = any(sections) or kept_any
    if not sections:
        status = "blocked"
        issues.append({"code": "EMPTY_PACKET", "severity": "blocker", "citation": None})
    elif not kept_any:
        status = "blocked"
        issues.append({"code": "EMPTY_PACKET", "severity": "blocker", "citation": None})
    elif packet_overflow:
        status = "needs_review"
    else:
        status = sliced.get("status") if sliced.get("status") in ("ok", "incomplete") else "ok"

    if not egress_approved:
        issues.append({
            "code": "EGRESS_APPROVAL_REQUIRED",
            "severity": "blocker",
            "citation": None,
        })

    has_blocker = any(i.get("severity") == "blocker" for i in issues)
    egress_ready = bool(
        egress_approved
        and not has_blocker
        and status in ("ok", "incomplete")
        and not packet_overflow
    )

    packet = {
        "version": "1.0",
        "source_sha256": digest,
        "total_pages": total_pages,
        "metadata": metadata,
        "sections": packet_sections,
        "ocr_required_pages": ocr_pages,
        "issues": copy.deepcopy(issues),
    }
    return {
        "version": "1.0",
        "source_sha256": digest,
        "status": status,
        "egress_approved": egress_approved,
        "egress_ready": egress_ready,
        "packet": packet,
        "issues": issues,
        "redactions": redactions,
        "local_only": local_only,
    }


def build_extraction_packet(
    sliced: dict[str, Any],
    *,
    egress_approved: bool = False,
    document_metadata: dict[str, Any] | None = None,
    allowed_columns: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build the egress-safe extraction packet from a slicer result.

    The packet is the only structure permitted to leave the host: rebuilt
    clean payloads with typed redactions, allowlisted metadata, and no
    headings, notes, thumbnails or attachments. Source text is untrusted
    data — it never grants egress, approval, or tool authority. Every line
    is accounted for: kept, redacted, or local-only with a citation.
    """
    code = "MALFORMED_INPUT"
    try:
        _require(isinstance(sliced, dict))
        _require(isinstance(egress_approved, bool))
        if document_metadata is not None:
            _require(isinstance(document_metadata, dict))
        if allowed_columns is not None:
            _require(isinstance(allowed_columns, (set, frozenset)))
            _require(all(isinstance(c, str) for c in allowed_columns))
        digest = sliced.get("source_sha256")
        _require(
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        )
        total_pages = sliced.get("total_pages")
        _require(_is_int(total_pages) and total_pages >= 1)
        sections = sliced.get("sections")
        _require(isinstance(sections, list))
        if len(sections) > MAX_SECTIONS:
            raise _Failure("INPUT_LIMIT_EXCEEDED")
        for section in sections:
            _validate_section(section, total_pages)
        columns = None if allowed_columns is None else frozenset(allowed_columns)
        return copy.deepcopy(
            _build(
                sliced,
                egress_approved=egress_approved,
                document_metadata=document_metadata,
                allowed_columns=columns,
            )
        )
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = "MALFORMED_INPUT"
    if code not in _CLOSED_CODES:
        code = "MALFORMED_INPUT"
    raise RentRollNormalizationError(code) from None


def egress_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Return the egress-ready packet payload, or refuse with a closed code.

    Refusal is typed: EGRESS_NOT_APPROVED when the host never approved
    egress, PACKET_NOT_READY when the packet is blocked or incomplete.
    """
    try:
        _require(isinstance(result, dict))
        _require(isinstance(result.get("packet"), dict))
        approved = result.get("egress_approved")
        ready = result.get("egress_ready")
        _require(isinstance(approved, bool))
        _require(isinstance(ready, bool))
        if not approved:
            raise _Failure("EGRESS_NOT_APPROVED")
        if not ready:
            raise _Failure("PACKET_NOT_READY")
        return copy.deepcopy(result["packet"])
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = "MALFORMED_INPUT"
    if code not in _CLOSED_CODES:
        code = "MALFORMED_INPUT"
    raise RentRollNormalizationError(code) from None
