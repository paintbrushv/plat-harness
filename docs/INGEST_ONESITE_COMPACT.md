# Compact OneSite BIFF XLS adapter

`plat_harness.ingest.onesite_compact.normalize_onesite_compact_xls(raw_bytes: bytes) -> dict`

This is a read-only, source-grounded **blocked-intake observation adapter**,
not an occupancy certifier. It extracts raw structural observations from RealPage
OneSite compact BIFF spreadsheet exports without executing macros, external links,
formulas, or financial rates.

## Supported source shape

An OLE/BIFF workbook (magic bytes `d0cf11e0a1b11ae1`), single sheet, independently
tested with `xlrd 2.0.2`:

1. **Title and Preamble**:
   - Preamble rows (rows 1–5) containing `RENT ROLL DETAIL` or `OneSite`.
   - Single worksheet required; multi-sheet workbooks raise `UNSUPPORTED_ONESITE_LAYOUT`.
2. **Column Layout**:
   - Compact export layout typically having ~20–30 columns (strictly fewer than 51 columns).
   - Detailed OneSite exports (which have 51+ columns and header at row 8 with unit at column 0
     and total billing at column 50) are rejected with `UNSUPPORTED_ONESITE_LAYOUT`.
   - Header row located at row 6 (or rows 5–11) with:
     - `Unit` (e.g. column 2 / C)
     - `Floorplan` (column 3 / D)
     - `Unit Designation` (column 4 / E)
     - `SQFT` (column 5 / F)
     - `Unit/Lease Status` (column 6 / G)
     - `Name` / `Resident` (column 7 / H)
     - `Lease Rent` / `RENT` (base rent columns)
     - Ancillary charge columns across rows (e.g. `TRASH`, `GARAGE`, `DAMAGE WAIVER PROGRAM`,
       `PET FEE`, `STORAGE`, `BIKE STORAGE`, `MODEL CONCESSION`)
3. **Section Handling and Boundaries**:
   - Only active inventory in current sections is parsed.
   - Non-current lease rows (`Applicant`, `Pending Renewal`, `Former resident`, `Future`, etc.)
     are excluded and emit `NON_CURRENT_LEASE_EXCLUDED` warnings.
   - Explicit non-current sections (`Future Residents`, `Former Residents`, `Applicants`,
     `Pending Renewals`) are excluded and emit `NON_CURRENT_SECTION_EXCLUDED` warnings.
   - Continuations following excluded non-current leases emit `NON_CURRENT_CONTINUATION_EXCLUDED`.
   - A `Totals:` or `Summary` row terminates inventory. Missing termination emits
     `INVENTORY_BOUNDARY_MISSING`.

## Unit & Charge Parsing Rules

- **Physical Inventory**:
  - Non-empty `Unit` with physical attributes defines a unit observation.
  - Physical unit identifiers are extracted for deduplication and contract-bound citations.
- **Charge Separation**:
  - Base rent columns (`RENT`, `Lease Rent`, `Base Rent`, `Market Rent`) are programmatically
    distinguished from ancillary charge columns.
  - Helper functions `classify_charge(code)`, `is_base_rent(code)`, and `is_ancillary_charge(code)`
    provide typed inspection.
  - Ancillary categories include `parking`, `pet`, `storage`, `utilities`, `washer_dryer`,
    `concessions`, and `insurance`.
  - Primary unit evidence cites base rent; non-zero ancillary charges in the same row
    attach as separate evidence items emitting `CONTINUATION_EVIDENCE` warnings.
- **PII Redaction**:
  - Tenant names in output are strictly `[REDACTED]`.
  - Raw resident names, phone numbers, and canary strings never appear in serialized output.
  - Cell citations contain only structural coordinates (`source_sha256`, `sheet`, `row`, `row_end`,
    `column`), never source text.
- **Status Mapping**:
  - Occupied states (`Occupied`, `Occupied-NTV`, `Occupied-NTVL`, `Occupied No NTV`, `Current`,
    `Notice`) map to standard status `occupied`.
  - Vacant states (`Vacant`, `Vacant-Leased`, `Vacant Not Leased`, `Vacant Leased`) map to standard
    status `vacant`.
  - Explicit down states (`Down`, `Offline`, `Out of Service`) map to standard status `down`.
  - Vendor category `Admin/Down` or unsupported statuses (`Model`, `Admin`) do not map to `down`;
    they map to `None` and emit `UNSUPPORTED_UNIT_STATUS`.
- **Unresolved Unit Use**:
  - Floorplans (`1A`, `2B`, `Studio`, etc.) and unit designations do **not** establish asset use
    (residential vs commercial).
  - Unless explicit use is stated, `unit_type` remains `None`.
  - Units with unresolved use are kept out of `residential_units`/`commercial_units` and recorded
    under `issues` with code `UNRESOLVED_UNIT_USE` containing the full unit `observation`.
  - When use is unresolved, overall status is `blocked` and scoped counts remain `None`.
  - `DOWN_EVIDENCE_UNRESOLVED` is emitted when independent Down evidence is not established.

## Safety & Limits

- Maximum input payload: 32 MiB (`33,554,432` bytes).
- Maximum worksheet rows: 50,000.
- Maximum worksheet columns: 128.
- Maximum worksheet cells: 250,000.
- Multi-sheet workbooks strictly rejected with `UNSUPPORTED_ONESITE_LAYOUT`.
- Detailed OneSite layouts (51+ columns) strictly rejected with `UNSUPPORTED_ONESITE_LAYOUT`.
- Read-only: `xlrd` with `logfile=_DiscardLog()` and `verbosity=0` to prevent input-bearing
  diagnostic leakage.
- Library warnings are treated as errors and sanitized as structural failures.
- Sanitized exceptions: only `RentRollNormalizationError(code)` is raised with closed codes:
  `MALFORMED_INPUT`, `INPUT_LIMIT_EXCEEDED`, `UNSUPPORTED_ONESITE_LAYOUT`, `UNSUPPORTED_XLS_FORMAT`,
  `XLS_DEPENDENCY_MISSING`. `__context__` and `__cause__` are stripped.

## Return Structure

Matches the v1-compatible normalized observation structure:

```json
{
  "version": "1.0",
  "pms_type": "realpage",
  "source_sha256": "<hex64>",
  "residential_units": [],
  "commercial_units": [],
  "counts": {
    "residential": {"occupied": null, "vacant": null, "down": null, "total": null},
    "commercial": {"occupied": null, "vacant": null, "down": null, "total": null}
  },
  "summary": {
    "residential": {"status": "absent", "reported_counts": null, "citations": []},
    "commercial": {"status": "absent", "reported_counts": null, "citations": []}
  },
  "issues": [
    {
      "code": "UNRESOLVED_UNIT_USE",
      "severity": "blocker",
      "citation": {
        "source_sha256": "<hex64>",
        "sheet": 1,
        "row": 7,
        "row_end": 7,
        "column": 5
      },
      "observation": {
        "unit_id": "101",
        "status": "occupied",
        "unit_type": null,
        "tenant_name": "[REDACTED]",
        "evidence": [
          {
            "unit_id": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 3},
            "status": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 7},
            "floorplan": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 4},
            "designation": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 5},
            "tenant_name": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 8},
            "charge": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 18}
          },
          {
            "charge": {"source_sha256": "<hex64>", "sheet": 1, "row": 7, "row_end": 7, "column": 19}
          }
        ]
      }
    }
  ],
  "status": "blocked"
}
```

## Synthetic Tests

All test workbooks are generated synthetically using `xlwt` into embedded base64/zlib BIFF8
fixtures and in-memory test books. No real or private deal data is copied or retained.
Canary strings verify complete PII redaction.
