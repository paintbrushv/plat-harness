# Multi-PMS rent roll normalizer — bounded ingestion contract

`plat_harness.ingest.normalize_rent_roll(stream, pms_type) -> dict` creates a
JSON-serializable, redacted observation with source-bound citations. It does not
call an LLM, network endpoint, service, financial engine, or persistence layer.
It computes only unit counts, not rents, financial metrics, or occupancy rates.
**Neither `normalized_unvalidated` nor a reconciled summary certifies underwriting.**

## Install and public dispatch

Install spreadsheet support with `pip install "plat-harness[ingest]"`, or from
a source checkout with `pip install -e ".[ingest]"`. Development/test installs
use `pip install -e ".[dev]"`. CSV remains standard-library-only.

The single stream API routes original BIFF XLS bytes to the detailed OneSite
blocked-intake adapter **only when `pms_type` selects RealPage/OneSite**. It
never overrides an explicitly selected PMS. See [PMS_ONESITE.md](PMS_ONESITE.md)
for that source-grounded layout and its mandatory uncertainty. PDF ingestion
is not implemented (`UNSUPPORTED_INPUT_FORMAT`).

## Supported flat inputs (not universal vendor support)

Tests use synthetic Yardi-, RealPage-, and Entrata-labelled flat export dialects.
No production vendor export has been validated by these tests. Resident ledgers,
PDFs, arbitrary vendor reports, cross-property portfolios, and arbitrary custom
status codes are not claimed as supported.

- UTF-8 comma-separated CSV: text or binary readable streams. A UTF-8 BOM is
  accepted. Quoted multiline cells and CRLF line endings are supported.
- XLSX: binary readable streams, using an optional, **lazy** `openpyxl` import.
  Tested with `openpyxl 3.1.5`. CSV requires only the Python standard library.
  Missing XLSX dependency raises `XLSX_DEPENDENCY_MISSING`. Optional readers
  are declared in the `ingest` extra; the module never installs packages.
- The stream is read from its **current position** and consumed, not closed or
  rewound. Use a fresh stream positioned at the beginning of the source. No
  filename, sheet title, workbook property, tenant ID, or arbitrary free text is
  returned. Binary streams are preferable for exact on-disk byte provenance.
- One property's inventory, with unit identifiers unique across all selected
  sheets. Explicit property/building columns must identify one consistent
  context throughout the current inventory (see below). All worksheets are
  inspected by content, not by title; arbitrary metadata sheets without an
  inventory header contribute no units and are flagged with a static warning.
  They can contain the scoped summary table described below. Hidden rows/sheets
  are not filtered. Worksheet positions are one-based; omitted worksheets still
  occupy a position. An ignored sheet is not proof that inventory is complete.
- Vendor preambles before a recognized header are ignored. Inventory header
  cells must all occur on one row; this is a multi-row **report**, not support for
  merged or multi-tier header bands. Repeated headers can change column order.
  A partial inventory header is **not** an ignored preamble: a recognized unit
  header before any active table, a unit header plus another recognized field,
  a singleton unit header, or both status and asset-use headers blocks all counts
  with `INCOMPLETE_INVENTORY_HEADER`. `Unit,Rent` on an additional sheet therefore
  cannot silently disappear merely because use/status are both missing.
  This applies on additional worksheets and before/after valid tables; later
  complete headers do not heal it. If no complete inventory header exists
  anywhere, the structural error remains `HEADER_NOT_FOUND`.

PMS aliases (case and surrounding/repeated whitespace are normalized):

| Canonical PMS | Accepted `pms_type` values |
|---|---|
| `yardi` | `yardi`, `yardi voyager`, `voyager` |
| `realpage` | `realpage`, `realpage onesite`, `onesite` |
| `entrata` | `entrata` |

Required headers (case/whitespace insensitive):

| PMS | Unit identifier | Occupancy status | Explicit asset use |
|---|---|---|---|
| Yardi | `Unit`, `Unit Number` | `Status`, `Unit Status` | `Unit Type`, `Space Type`, `Property Type` |
| RealPage | `Unit`, `Unit #`, `Unit Number` | `Status`, `Occupancy Status` | `Unit Type`, `Unit Category`, `Space Type` |
| Entrata | `Unit`, `Unit Number` | `Status`, `Occupancy` | `Unit Type`, `Space Type` |

Optional headers, common to these dialects:

- `Resident`, `Resident Name`, `Tenant`, `Tenant Name` → `tenant_name`.
- `Phone`, `Phone Number`, `Telephone` → `phone`.
- `Email`, `Email Address`, `E-mail` → `email`.
- `Record Type`, `Row Type` → explicit continuation/exclusion handling only;
  the source value is never returned.
- `Property`, `Property ID`, `Property Code`, `Property Name` → internal property
  context only, never returned.
- `Building`, `Building ID`, `Building Number`, `Building #`, `Building Name` →
  internal building context only, never returned.

A recognized field appearing more than once in a candidate header is ambiguous
and rejected. Unknown columns are discarded, even if labelled rent, tenant ID,
or notes. **Building/property columns do not extend the output unit key**.
Instead, their presence is an enforced single-context constraint:

- Each present context column must be explicit and nonblank on every current
  unit/continuation row; no carry-forward is inferred. Formulas, unsupported
  cell types, and non-General numeric display formats are rejected with
  `INVALID_UNIT_CONTEXT`.
- The tuple of property/building values must match exactly after surrounding
  whitespace removal across all current rows and worksheets. Adding/removing a
  context column between current tables also fails with `AMBIGUOUS_UNIT_CONTEXT`.
  Different contexts are rejected even when their unit IDs do not collide.
- Either context failure blocks all counts and clears both unit arrays; a later
  good row cannot heal it. No context value or context evidence-map field is
  emitted, and output version/shape remains unchanged.
- When no explicit context columns exist anywhere, legacy same-ID deduplication
  is retained. This is not proof of global uniqueness or complete coverage.
  Unrecognized context labels/preambles cannot establish identity. Multi-context
  reports require a reviewed upstream shape adapter, not silent deduplication.

## Unit identity, classification, and duplication

An ID must match `(?:[A-Z]{1,3}-?)?[0-9]{1,4}[A-Z]?` after surrounding whitespace
is removed. Examples: `101`, `0101`, `A-101`, `C1`. Numeric XLSX integers are
accepted only with `General` number formatting. A numeric identifier with any
other format (including `0000`, `0`, scientific notation, or `@`) is rejected
with `UNSUPPORTED_UNIT_ID_FORMAT` and a citation to the exact source ID cell;
the numeric storage value is never silently substituted for its display identity.
Store leading-zero IDs as text: text `0101` is preserved even if the cell has a
numeric display format. Date-converted/unsupported values remain invalid IDs.
Formatting on discarded amount columns does not invalidate an otherwise valid
inventory; this module does not interpret their financial values.
Names, all-letter identifiers, email addresses, obvious telephone numbers,
embedded whitespace, arbitrary paths, and formulas fail closed. This is a
conservative syntax constraint, not a universal PII detector or identity proof.

Asset use is explicit: `Residential`/`Apartment` map to `residential`;
`Commercial`/`Retail`/`Office` map to `commercial`. These uses have separate unit
arrays and separate four-count inventories. A floorplan such as `1BR`, a blank
use, parking/storage, or a mixed-use label is unsupported; residential use is
never inferred from a filename or tenant name.

Supported occupancy labels (case/whitespace insensitive):

| Output | Input labels |
|---|---|
| `occupied` | `Occupied`, `Current`, `Notice`, `Notice Rented`, `Notice Unrented` |
| `vacant` | `Vacant`, `Vacant Rented`, `Vacant Unrented` |
| `down` | `Down`, `Offline`, `Out of Service` |

`Leased`, `Model`, `Admin`, combined labels, unknown codes, and blanks do not
establish physical occupancy and are blockers. These mappings are the contract
for the supported dialects, not a claim about every PMS installation.

- Repeated rows with the same validated ID, status, and normalized use are merged
  with **all citations retained** and `DUPLICATE_UNIT_EVIDENCE`. They count once;
  this warning still requires review of global unit-key uniqueness.
- Explicit `charge`, `co-tenant`, or `cotenant` record types may refer only to an
  already observed, explicit unit ID. They may leave status/use blank, inheriting
  those values from the earlier cited observation; supplied values must agree.
  No blank-ID carry-forward or inferred co-tenant grouping is performed.
- A conflicting status/use removes that unit from both result arrays, blocks
  counts, and emits `DUPLICATE_UNIT_CONFLICT`. Subsequent rows cannot heal it.
  Other malformed observations also invalidate counts, even if some earlier
  valid observations remain in the arrays. Arrays are observations, not an
  alternative route around blockers.
- Explicit status/record type `Applicant`, `Future`, `Future Resident`, or
  `Future Tenant` is excluded with a warning. A single-cell section marker
  `Future Residents`, `Future Tenants`, `Applicants`, or `Future/Applicants`
  excludes subsequent inventory rows until `Current Residents`, `Current Units`,
  `Current Tenants`, or `Units`. An intervening repeated inventory header alone
  does **not** end a future section. Unrecognized sections inside an active
  inventory table fail closed instead of being guessed.

## Four counts and summary reconciliation

Each scope always has `occupied`, `vacant`, `down`, and `total` keys under
`counts`. When the enumerated current-unit inventory has valid identifiers,
statuses, and uses, its denominator is the unique current-unit row count.
It is **not independent evidence of complete property coverage**. Missing
summaries remain explicitly `absent`; no summary is invented.

An inventory ambiguity makes **all four values `null` in both scopes**, not
zero. No current units at all is a blocker with null counts. If one scope has
valid units and the other has none, the empty scope has four zero counts, with
its zero denominator reflecting that enumeration—not an occupancy percentage.

The supported summary shape is a separate, column-order-independent table:

```csv
Scope,Occupied,Vacant,Down,Total
Residential,2,1,1,4
Commercial,1,1,0,2
```

`Summary Scope` can replace `Scope`. Scope values can also be `Residential Summary`
or `Commercial Summary`. All four counts must be explicit unsigned decimal
integers of at most six digits. Formula cells, blanks, negative values, floats,
and an unknown scope are not filled in. Only the recognized scope plus the four
count columns are used; additional in-header columns are discarded.

Each `summary` scope contains `status`, `reported_counts`, and a list of count
cell `citations`. Its status is one of:

- `absent`: no supported scoped summary was found;
- `unresolved`: valid summary values exist, but inventory cannot be counted;
- `reconciled`: each reported count equals the row-derived count;
- `mismatch`: at least one reported count differs, including an inconsistent
  total. Overall result is blocked; observed counts are not overwritten;
- `invalid`: malformed or conflicting repeated summary evidence. Later rows do
  not repair it; reported counts are null and citations remain available.

Identical repeated summaries retain citations and emit a warning. An unscoped
`Total`, `Totals`, `Grand Total`, or `Report Total` row in an inventory table is
not counted as a unit: it emits blocker `UNSCOPED_REPORT_TOTAL`. Arbitrary vendor
report subtotals need an explicit adapter; silently dropping a conflicting
reported denominator is not acceptable.

## Output and privacy boundary

The root allowlist is exactly:

`version`, `pms_type`, `source_sha256`, `residential_units`, `commercial_units`,
`counts`, `summary`, `issues`, `status`.

Version is `1.0`. Unit objects have `unit_id`, canonical `status`, canonical
`unit_type`, and `evidence`. When recognized tenant-name/phone/email headers
exist, their fields are emitted only as `[REDACTED]`, including when blank.
Unknown columns and tenant identifiers have no output fields. Rent and all other
amounts are intentionally omitted, so no precision/rounding claim is made.

Evidence is a list of field-to-citation maps, retaining observed fields only.
A continuation's inherited values reference the earlier evidence rather than a
fabricated citation to its blank cells. Every cell citation has this shape:

```json
{"source_sha256":"<hex SHA256>","sheet":1,"row":4,"row_end":4,"column":1}
```

For XLSX, `sheet` is the physical worksheet position and rows/columns are
one-based worksheet coordinates. For CSV, `sheet` is always 1, `column` is the
one-based CSV field position, and `row`/`row_end` span the logical record's
physical source lines (including quoted newlines). This locates a field in a
record, not a byte offset or the display line of an individual multiline cell.
The hash covers consumed binary source bytes, including a BOM and ZIP metadata;
for text streams it covers the consumed text re-encoded as UTF-8. Text-mode
newline translation can therefore differ from the on-disk byte hash.

Flat-parser issues contain only static `code`, `severity` (`warning`/`blocker`),
and `citation` (or null), never source values or raw records. The OneSite BIFF
adapter additionally permits allowlisted, redacted `observation` objects on
`UNRESOLVED_UNIT_USE` and `ONESITE_REPORT_SUMMARY` issues, documented separately;
these unresolved observations are not scoped occupancy or engine inputs. Blockers set overall
`status` to `blocked`; otherwise it is `normalized_unvalidated`.

Structural failures raise `RentRollNormalizationError` with a static `.code`
and generic message: `EMPTY_INPUT`, `UNSUPPORTED_PMS`, `HEADER_NOT_FOUND`,
`AMBIGUOUS_HEADER`, `MALFORMED_INPUT`, `INPUT_LIMIT_EXCEEDED`, or
`XLSX_DEPENDENCY_MISSING`. Dispatch also returns `UNSUPPORTED_INPUT_FORMAT`
for PDFs and `UNSUPPORTED_PMS_FORMAT` for BIFF with another selected PMS;
OneSite-specific codes are documented in PMS_ONESITE.md. Original exceptions
are not chained or retained in
`__context__`. Input-bearing library warnings are converted to sanitized errors
before emission. The normalizer itself does not log or persist raw inputs.
Caller-owned streams remain caller-owned sensitive material; do not enable
traceback-local-variable dumps, persist raw streams, or log source objects.
Python's warning-filter context has interpreter-dependent concurrency semantics;
use isolated ingestion workers if concurrent application warning handling matters.

## Limits and integration

Input limit: 8 MiB; XLSX declared uncompressed ZIP total: 32 MiB and at most 1,024
entries. Parsing bounds: 50,000 physical CSV lines / cumulative XLSX rows, 128
columns, and 250,000 cells. XLSX declared dimensions are checked, then every
worksheet's raw XML coordinates are preflighted **before any row iterator** can
allocate padded sparse rows. Explicit and inferred coordinates, row gaps,
padded row widths, and cumulative worksheet budgets are bounded. Invalid,
duplicate/nonmonotone, and mismatched row/cell coordinates are rejected rather
than silently overwritten or relocated. The XML source hook is isolated and
tested against openpyxl 3.1.5. Actual rows are then read independently of declared
dimensions so understated dimensions cannot hide inventory; iterator-side limits
remain as a second check.
Limits are defensive bounds, not a general hostile-document sandbox. XLSX macros
and external-link archives are rejected. Outside the separate OneSite adapter,
XLS is unsupported. XLSM, encrypted files, other CSV
encodings/delimiters, merged headers, and formula-derived required fields are
out of scope. Formulas are never evaluated. Optional library warnings can reject
otherwise readable workbooks; do not bypass that fail-closed behavior blindly.

Example using synthetic data only:

```python
from io import StringIO
from plat_harness.ingest import normalize_rent_roll

result = normalize_rent_roll(StringIO(
    "Unit,Status,Unit Type,Resident Name\n"
    "101,Current,Residential,Synthetic Resident\n"
    "102,Vacant,Residential,\n"
), "Yardi Voyager")
assert result["counts"]["residential"] == {
    "occupied": 1, "vacant": 1, "down": 0, "total": 2,
}
assert result["summary"]["residential"]["status"] == "absent"
assert result["status"] == "normalized_unvalidated"
```

Synthetic contract tests are in `tests/test_pms_normalizer.py`, covering CSV text,
binary CSV, XLSX, all three dialects, exact residential/commercial counts,
summary states, duplicates/conflicts/continuations, future exclusions, missing
or ambiguous evidence, source citations, and PII canaries in fields, metadata,
errors, warnings, and free text. Additional regression-first security tests in
`tests/test_pms_normalizer_security.py` cover multi/missing context, formatted
numeric IDs, partial inventory worksheets, and pre-allocation XML coordinate
limits. Hostile-coordinate tests use an iterator sentinel rather than allocating
oversized rows. These tests are implementation evidence, **not independent
review or approval**. Live-export validation and upstream/downstream
integration remain parent-owned; this intermediate must not bypass existing
occupancy, evidence, or approval gates.
