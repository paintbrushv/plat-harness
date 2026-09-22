# OM Section Slicer

`plat_harness.ingest.om_section_slicer.slice_om_sections(raw_bytes: bytes, *, toc_pages=None, page_budget=None) -> dict`

Read-only, local-only selection of candidate financial sections from a
multi-page offering memorandum (OM) PDF. This module slices pages; it does not
normalize, interpret, or verify any figure. Extraction is bounded and
deterministic — no OCR, no cloud calls, no model inference, no numeric
synthesis.

---

## Contract

**Selection target:** ordered financial section codes, matched by heading
line on each non-TOC page (first match per page wins; registry order puts
`tax_assumptions` before generic statement aliases so broker tax prose never
lands in `operating_statements`):

| Code | Heading aliases (representative) |
| --- | --- |
| `tax_assumptions` | Tax Assumptions, Broker Tax Assumptions |
| `operating_statements` | Operating Statement(s), Trailing Twelve Month Operating Statement, P&L Statement, Income and Expense Statement |
| `t12` | T12, T.12, Trailing Twelve, Twelve Month Operating Statement |
| `rent_roll` | Rent Roll Summary, Rent Roll |
| `debt_structure` | Debt Structure, Financing Assumptions, Loan Summary, Current Debt |
| `lease_terms` | Lease Summary, Lease Terms, Lease Abstracts, Rent Schedule |

**Output shape** (root keys): `version` (`"1.0"`), `source_sha256`,
`total_pages`, `sections`, `selected_pages`, `ocr_required_pages`, `status`,
`issues`. Each section carries:

- `code`, `heading_text`, `heading_citation`
- `page_start` / `page_end` — original 1-indexed page bounds
- `text_lines` — verbatim local text extraction; digits keep their original
  formatting, no amounts are parsed, summed, or synthesized
- `attribution: "broker_claim"` and `provenance: "unverified_broker_provided"`
  — every figure in a selected section remains an unverified broker claim

**Citations** follow the shared grid schema
`{"source_sha256", "sheet", "row", "row_end", "column"}` where `sheet` is the
1-indexed PDF page and `row` the 1-indexed line on that page.

**Statuses** (mutually exclusive):

- `ok` — candidate sections found, within budget, fully extractable.
- `incomplete` — candidate sections found but one or more candidate pages have
  no extractable text layer (`OCR_REQUIRED` warnings; those pages are listed
  in `ocr_required_pages` and are never invented).
- `needs_review` — candidate financial pages exceed `page_budget`; a
  `PAGE_BUDGET_OVERFLOW` blocker carries `total_candidate_pages` and
  `overflow_pages`. The fixed budget never silently cuts required financial
  context: the slicer reports the overflow and bounds `selected_pages` to the
  budget, leaving the host to widen the budget or review explicitly.
- `blocked` — no financial sections found at all (`NO_FINANCIAL_SECTIONS`).

## TOC handling

TOC pages are detected by a "table of contents"/"contents" title plus dotted
leader entries, and are never selected as financial context; selection
resolves to the actual heading pages instead. Callers may pass explicit
`toc_pages={...}` (1-indexed) to force exclusions; auto-detection and the
explicit set combine. TOC detection requires either two dotted entries or one
dotted entry naming a known section word, so a narrative page that happens to
contain a dotted line is not misclassified.

## Bounds and error codes

Failures raise `RentRollNormalizationError` (shared sanitized exception) with
closed codes only — `MALFORMED_INPUT` (non-bytes, non-PDF magic, corrupt
syntax, invalid `toc_pages`, non-positive `page_budget`), `INPUT_LIMIT_EXCEEDED`
(size / page / total-character budgets), `EMPTY_INPUT`,
`PDF_DEPENDENCY_MISSING`, `EXTRACTION_FAILURE` (pdfplumber raised mid-document;
the source text never leaks into the message, and exceptions are never
chained). Operational bounds: 32 MiB, 500 pages, 2,000,000 total extracted
characters, `DEFAULT_PAGE_BUDGET = 40` pages.

## Isolation

Registry (`financial_section_codes()`) and result objects are returned as deep
copies; caller mutation of a returned structure never leaks into later loads
or module-level state.

## Synthetic testing

All tests in `tests/test_om_section_slicer.py` build PDFs in-memory via
reportlab — no real deal bytes, no private filenames, no tenant rows. Tests
cover: TOC versus actual table resolution (explicit and auto-detected), absent
TOC, repeated headings extending (never duplicating) sections, original page
bounds and verbatim text, broker-claim attribution, budget overflow honesty,
scanned-page OCR-required reporting, no-section blocked status, closed error
codes with sanitized messages, extraction-failure sanitization via a
fault-injection mock confined to the extraction seam, and deep-copy isolation
of registry and results.
