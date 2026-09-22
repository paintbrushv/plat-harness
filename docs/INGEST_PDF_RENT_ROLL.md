# Summit / East Quarter PDF Rent Roll Adapter

`plat_harness.ingest.pdf_rent_roll.normalize_pdf_rent_roll(raw_bytes: bytes) -> dict`

This is a read-only, source-grounded **blocked-intake observation adapter** for Summit, East Quarter, and similarly structured multi-page PDF rent rolls. It extracts unit inventory and charge observations into the v1-compatible normalization structure without calling financial engines or computing unauthorized underwriting metrics.

---

## Supported Source Shape & Layout Signature

The adapter detects and processes PDF rent rolls conforming to the 150 Summit / East Quarter layout standard:

- **PDF Magic:** Input must begin with `b'%PDF-'`.
- **Multi-page Structure:** Multi-page PDF document (>= 2 pages) with `Rent Roll` title in the header band of the first page.
- **Split Header Band:**
  - **Line A:** Logical columns containing `Unit`, `Unit Type`, `Resident Name` (or `Name`), `Market`, `Resident` (or `Actual`), `Lease`, `Move Out`, `Balance`.
  - **Line B:** Second line of split headers containing `Sq Ft`, `Rent`, `Deposit`, `Expiration`.
  - **Folded Columns:**
    1. `Unit` (column 1)
    2. `Unit Type` (column 2)
    3. `Sq Ft` (column 3)
    4. `Resident Name` (column 4)
    5. `Market Rent` (column 5)
    6. `Resident Rent` (column 6) [Base Rent]
    7. `Resident Deposit` (column 7) [Deposit]
    8. `Deposit` (column 8) [Deposit / Other Deposit]
    9. `Lease Expiration` (column 9)
    10. `Move Out` (column 10)
    11. `Balance` (column 11)
- **Section Headers:**
  - Current inventory section: `Current/Notice/Vacant Residents` (or `Current Residents`, `Current Units`).
  - Non-current sections: `Future Residents`, `Future Residents/Applicants`, `Applicants`, `Former Residents` (excluded with `NON_CURRENT_SECTION_EXCLUDED` warnings).
  - Termination: Document totals or summary sections (`Totals:`, `Summary Groups`, `Total <Property>`) terminate extraction immediately.
- **Repeated Multi-Page Headers:** Repeated header bands and preambles across subsequent pages are cleanly skipped without double-counting units.

Non-matching PDF layouts raise `RentRollNormalizationError('UNSUPPORTED_PDF_LAYOUT')`.

---

## Contract and Uncertainty

The output adheres strictly to the v1 normalization contract:
- **Root Keys:** `version`, `pms_type`, `source_sha256`, `residential_units`, `commercial_units`, `counts`, `summary`, `issues`, `status`.
- **PMS Type:** `"realpage"`.
- **Citations:** 1-indexed cell citations matching the v1/v2 grid citation schema:
  `{"source_sha256": digest, "sheet": page_num, "row": line_num, "row_end": line_num, "column": col_idx}`
  where `sheet` is the 1-indexed PDF page number, `row` is the 1-indexed line number on that page, and `column` is the 1-indexed folded column index (1 to 11).
- **PII Redaction:** Tenant names are strictly `[REDACTED]`. No raw names, phone numbers, or emails appear in serialized outputs or citation payloads.
- **Explicit Use Classification:** Floorplans (e.g. `1BR`, `2BR`, `Studio`, `eqa8`, `4056-2`) do **not** establish residential or commercial use. Unresolved units have `unit_type=None`, emit `UNRESOLVED_UNIT_USE` blocker issues carrying the unvalidated unit observation, and leave `status = "blocked"` with null counts. Explicit use indicators (`"Residential"`, `"Commercial"`) populate the respective scoped unit lists and integer counts.
- **Status Mapping:**
  - Standard tenant names / IDs map to `"occupied"`.
  - `"VACANT"`, `"VACANT VACANT"`, `"Vacant Unrented"` map to `"vacant"`.
  - `"DOWN"`, `"DOWN DOWN"`, `"OFFLINE"`, `"OUT OF SERVICE"` map to `"down"`.
  - Unrecognized administrative designations (`"MODEL"`, `"ADMIN"`) emit `UNSUPPORTED_STATUS` and invalidate inventory completeness.
- **Charge Separation:** Base rent (`Resident Rent`, column 6) is cited under `evidence.charge`. Deposits (`Resident Deposit` col 7, `Deposit` col 8) are separated from base rent.

---

## Safety, Bounds, and Error Codes

- **Sanitization:** Library warnings and exceptions from `pdfplumber` / `pdfminer` are trapped and sanitized without exception chaining (`from None`), preventing source text exposure in logs or tracebacks.
- **Operational Bounds:**
  - Maximum bytes: 32 MiB (`33,554,432` bytes)
  - Maximum pages: 500 pages
  - Maximum lines: 50,000 lines
  - Maximum cells: 250,000 cells
- **Closed Error Codes:** All failures raise `RentRollNormalizationError` with one of five static codes:
  1. `MALFORMED_INPUT`: Corrupted PDF syntax, non-bytes input, or non-PDF magic.
  2. `INPUT_LIMIT_EXCEEDED`: File size, page count, line count, or cell count exceeds thresholds.
  3. `UNSUPPORTED_PDF_LAYOUT`: Document is single-page, lacks "Rent Roll" title, lacks split headers, or lacks current section header.
  4. `EMPTY_INPUT`: Empty or whitespace-only byte stream.
  5. `PDF_DEPENDENCY_MISSING`: `pdfplumber` or `pdfminer` library is unavailable.

---

## Synthetic Testing & Verification

All unit tests in `tests/test_pdf_rent_roll.py` generate synthetic multi-page PDF documents in-memory using `reportlab`. No real or private deal data is ever copied into test suites. Tests verify:
- Magic check and corrupted stream rejection.
- Size and page count limit enforcement.
- Split-header folding and multi-page repeated header deduplication.
- Exact 1-indexed citation coordinates (`sheet`, `row`, `row_end`, `column`, `source_sha256`).
- Status mapping (`occupied`, `vacant`, `down`, `unsupported`).
- PII redaction invariants across JSON serialization.
- Non-current section exclusion and summary termination.
- Closed error codes and absence of exception chaining.
