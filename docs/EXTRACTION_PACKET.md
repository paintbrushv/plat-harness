# Extraction Packet (Egress-Safe)

`plat_harness.ingest.extraction_packet.build_extraction_packet(sliced, *, egress_approved=False, document_metadata=None, allowed_columns=None) -> dict`

`plat_harness.ingest.extraction_packet.egress_payload(result) -> dict`

Builds the single structure permitted to leave the host from a slicer
result (`om_section_slicer.slice_om_sections` output or a slicer-shaped
dict). The packet is a rebuilt clean payload — allowlisted keys, typed
redactions, original citations — not a visually blacked-out copy of the
source. Broker figures remain broker claims; no amounts are parsed, summed,
or synthesized.

---

## Egress authority

Source content is **untrusted data, never authority**. It cannot
self-authorize egress, grant approvals, or inject tool instructions:

- `egress_approved` is the only egress grant. Without it the result carries
  an `EGRESS_APPROVAL_REQUIRED` blocker, `egress_ready` is `False`, and
  `egress_payload` raises `EGRESS_NOT_APPROVED`.
- Text inside the document claiming "EGRESS APPROVED" or similar has no
  effect on the decision.
- Broker-claim OM material is confidential by default: approval is an
  explicit host decision, never a default.

`egress_payload(result)` returns a deep copy of `result["packet"]` only when
`egress_approved` and `egress_ready` are both true; otherwise it raises a
typed refusal (`EGRESS_NOT_APPROVED` / `PACKET_NOT_READY`).

## Result shape

Root keys (exact set): `version` (`"1.0"`), `source_sha256`, `status`,
`egress_approved`, `egress_ready`, `packet`, `issues`, `redactions`,
`local_only`.

- `status`: `ok` / `incomplete` (OCR-required pages present) / `needs_review`
  (packet budget overflow) / `blocked` (no sections or no shippable content).
- `redactions`: typed redaction records
  (`{"reason_code", "citation"}` with the shared grid citation schema);
  reason codes: `email`, `phone`, `pii` (SSN-shaped tokens), `person_name`
  (labeled `Resident:`/`Tenant:`/… names), `metadata_redaction`.
- `local_only`: every line excluded from the packet, each with a
  `reason_code`, 1-indexed `line_index`, and grid citation.

`packet` keys (exact set): `version`, `source_sha256`, `total_pages`,
`metadata`, `sections`, `ocr_required_pages`, `issues`. Each packet section
carries only `code`, `page_start`, `page_end`, `attribution`, `lines`,
`rows` — headings, provenance, internal notes, thumbnails and attachments
never enter the packet. Each kept line is
`{"text", "line_index", "citation"}`.

## PII handling

Typed redaction replaces the matched token with a typed placeholder
(`[REDACTED:email]`, `[REDACTED:phone]`, `[REDACTED:pii]`,
`[REDACTED:person_name]`); surrounding text survives for financial context.
Ungrouped 3+ digit runs that are not financial figures (`1,000,000`) or
years look like addresses/contact prose — they cannot be safely
token-redacted, so the whole line stays local-only
(`UNSAFE_PII_LOCAL_ONLY`). Prompt-injection patterns in source text
("ignore previous instructions and approve…") are excluded with
`PROMPT_INJECTION_SUSPECT`. A fail-closed self-check re-scans every
redacted payload; a leak raises sanitized `REDACTION_FAILURE` instead of
shipping.

## Truncation honesty

- A line longer than `MAX_LINE_CHARS` (2,000) is excluded **whole**
  (`LINE_BUDGET_EXCEEDED` with a citation) — never cut mid-line, which
  would silently lose trailing footnote scope.
- Total packet text beyond `MAX_PACKET_CHARS` (200,000) raises status to
  `needs_review` with a `PACKET_BUDGET_OVERFLOW` blocker carrying
  `omitted_lines`; every omitted line is cited in `local_only`, egress is
  not ready, and `egress_payload` raises `PACKET_NOT_READY`.
- Accounting invariant: kept lines + `local_only` entries == input lines.

## Structured rows

Structured `rows` on a section require an explicit host-approved
`allowed_columns` set; without one the rows stay local and a
`STRUCTURED_COLUMNS_UNAPPROVED` warning is issued (unknown/unapproved
columns are dropped, never guessed). Approved cells are redacted with the
same typed rules as lines.

## Document metadata

Only `METADATA_ALLOWLIST` keys (`title`, `subject`, `producer`, `creator`,
`pages`) enter the packet — author and custom keys are dropped whole.
Values are typed-redacted; unclassifiable digit-bearing values and values
over `MAX_METADATA_CHARS` (200) stay local.

## Error codes

Failures raise `RentRollNormalizationError` (sanitized, never chained) with
closed codes only: `MALFORMED_INPUT` (non-dict input, bad argument types,
non-sha256 digest, bad section shapes), `INPUT_LIMIT_EXCEEDED` (more than
`MAX_SECTIONS = 500` sections), `EGRESS_NOT_APPROVED`, `PACKET_NOT_READY`,
`REDACTION_FAILURE`. Source text never leaks into exception messages.

## Isolation

Results are returned as deep copies and the input dict is never mutated;
caller tampering with a returned result cannot poison later builds.

## Synthetic testing

All tests in `tests/test_extraction_packet.py` use slicer-shaped synthetic
dicts or in-memory reportlab PDFs — no real deal bytes, no private
filenames, no tenant rows. Canaries (resident name, email, phone, address
prose, injection prose) are asserted absent from every serialized result
and payload, including end-to-end through the real slicer.
