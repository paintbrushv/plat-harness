# Ledger / Charge-Detail XLSX Adapter

`plat_harness.ingest.ledger_xlsx.normalize_ledger_xlsx(raw_bytes: bytes) -> dict`

This is a read-only, source-grounded **blocked-intake observation adapter**,
not an occupancy certifier. It extracts structural observations from unknown-vendor
OOXML XLSX workbooks whose physical-unit sheet carries ledger and charge-detail
rows (Bldg-Unit, Unit Status, Ledger, Charge Code, Scheduled Charges, Expected
Move-Out). It does not execute macros, external links, formulas, or financial rates.
Vendor identity is not inferred.

## Supported source shape

An OOXML spreadsheet workbook (magic bytes `PK`):

1. **Sheet selection by headers, never by title**:
   - The inventory worksheet is the first sheet whose header row contains
     `Bldg-Unit`, `Unit Status`, `Charge Code`, and `Scheduled Charges`
     (case-insensitive, hyphen/slash normalized). Worksheet names are ignored.
   - A separate `Report Parameters` (or any other non-matching) sheet is not
     inventory. Extra sheets emit `NON_INVENTORY_SHEET_IGNORED`.
2. **Header band** (typically row 7; row index is discovered, not hardcoded):
   - `Bldg-Unit`, `Unit Type`, `SQFT`, `Unit Status`, `Resident`, `Market Rent`,
     `Ledger`, `Charge Code`, `Scheduled Charges`, `Balance`, `Deposit Held`,
     `Move-In`, `Lease Start`, `Lease End`, `Expected Move-Out` (and similar aliases).
   - `Lease`, `Lease ID`, `Lease Start`, and `Lease End` are never treated as rent
     or charge-code columns.
3. **Charge / ledger rows**:
   - One physical identity may have several ledgers and charge codes.
   - Blank `Bldg-Unit` continuation rows, and repeating `Bldg-Unit` rows that add
     charges without a conflicting status, attach to the active unit.
   - Continuation rows **never inflate physical unit counts**.
4. **Building-relative identity**:
   - Compound IDs remain distinct (`A-101` vs `B-101`). The adapter does not
     strip the building prefix.

## Unit, use, and charge rules

- **Status** comes from `Unit Status`, not from tenant name.
  - Occupied / Current / Notice → `occupied`
  - Vacant → `vacant`
  - Down / Offline / Out of Service → `down`
  - Empty status → `MISSING_UNIT_STATUS` blocker; status remains `None`
  - `Admin/Down`, `Model`, and other unrecognized labels → `UNSUPPORTED_STATUS`;
    they are not independent Down evidence
- **Use**:
  - Explicit labels `Residential` / `Apartment` / `Commercial` / `Retail` / `Office`
    resolve scope.
  - Floorplans (`1BR`, `Studio`, `A1`) do **not** establish use (`UNRESOLVED_UNIT_USE`).
  - Labelled retail/commercial inventory cannot disappear: it is emitted on
    `commercial_units`.
  - Unresolved use keeps scoped counts `None` and `status` `blocked`. Missing
    status/use never invents occupancy economics.
- **Charge classification** (`classify_charge` / `is_base_rent` / `is_ancillary_charge`):
  - Ancillary categories are applied **before** base rent so `Pet Rent` is `pet`
    and `Free Rent` is `concessions`, not `base_rent`.
  - `Lease ID` / `Lease Start` / `Lease End` / `Lease` classify as `unknown`, never rent.
  - Categories: `parking`, `pet`, `storage`, `utilities`, `washer_dryer`, `concessions`.
- **PII**: tenant names in output are strictly `[REDACTED]`. Citations contain only
  `source_sha256`, 1-indexed `sheet` / `row` / `row_end` / `column`.

## Safety and limits

- Maximum input payload: 32 MiB (`33,554,432` bytes).
- Maximum worksheet rows: 50,000.
- Maximum worksheet columns: 128.
- Maximum worksheet cells: 250,000.
- Zip inspection rejects encrypted entries, VBA macros (`vbaProject.bin`), and
  `xl/externalLinks/`.
- Worksheet XML coordinates are bounded **before** openpyxl sparse-row allocation.
- `openpyxl` is loaded lazily with `read_only=True`, `data_only=False`, `keep_links=False`.
- Library warnings are treated as errors and sanitized.
- The only raised type is `RentRollNormalizationError(code)` with closed codes:
  `MALFORMED_INPUT`, `INPUT_LIMIT_EXCEEDED`, `UNSUPPORTED_XLSX_FORMAT`, `EMPTY_INPUT`,
  `UNSUPPORTED_XLSX_LAYOUT`, `XLSX_DEPENDENCY_MISSING`. `__context__` and `__cause__`
  are stripped.

## Return structure

Matches the v1-compatible normalized observation:

```json
{
  "version": "1.0",
  "pms_type": "unknown",
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
  "issues": [],
  "status": "blocked"
}
```

When every unit has resolved use and supported status, scoped unit lists are
populated, integer counts are derived, and `status` is `normalized_unvalidated`.
Output is an unvalidated observation; it is not occupancy approval or engine input.

## Synthetic tests

All public tests build in-memory openpyxl workbooks. No real or private deal files
are read. Canary strings verify complete PII redaction.
