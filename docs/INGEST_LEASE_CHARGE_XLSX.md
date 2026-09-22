# Abilene Split-Header / Lease-Charge XLSX Adapter

`plat_harness.ingest.lease_charge_xlsx.normalize_lease_charge_xlsx(raw_bytes: bytes) -> dict`

This is a read-only, source-grounded **blocked-intake observation adapter**,
not an occupancy certifier. It extracts raw structural observations from OOXML XLSX
workbooks with split column headers and charge continuation rows (such as Abilene
asset-level exports), without executing macros, external links, formulas, or financial rates.

## Supported source shape

An OOXML spreadsheet workbook (magic bytes `PK\x03\x04`):

1. **Title and Preamble**:
   - Worksheet containing a header cell in rows 1–4 matching `Rent Roll with Lease Charges`
     (case-insensitive, normalized whitespace).
   - If multiple worksheets are present, non-inventory worksheets are safely bypassed and
     flagged with `NON_INVENTORY_SHEET_IGNORED`.
2. **Split Headers (Rows 5–6)**:
   - Row 5: `Unit`, `Unit Type`, `Unit`, `Resident`, `Name`, `Market`, `Charge`, `Amount`, `Resident`, `Move In`, `Lease`, `Move Out`, `Balance`
   - Row 6: `Sq Ft`, `Rent`, `Code`, `Deposit`, `Deposit`, `Expiration`
   - Headers fold vertically into 12 logical columns:
     - `Unit`: physical unit identifier
     - `Unit Type`: floorplan or use designation
     - `Sq Ft`: physical unit square footage (`Unit` + `Sq Ft`)
     - `Resident Name`: tenant name (from `Resident` + `Name` or `Resident Name`)
     - `Market Rent`: scheduled/market rent (`Market` + `Rent`)
     - `Charge Code`: billing transaction code (`Charge` + `Code`)
     - `Amount`: charge billing amount
     - `Deposit`: deposit amount (`Resident` + `Deposit` or `Deposit`)
     - `Move In`: resident move-in date
     - `Lease Expiration`: lease term end date (`Lease` + `Expiration`)
     - `Move Out`: resident notice / move-out date
     - `Balance`: resident ledger balance
3. **Section Boundary (Row 7)**:
   - Section header at row 7: `Current/Notice/Vacant Residents` (or `Current Residents`, `Current Units`).
   - Only active inventory in this section is parsed.
   - Subsequent non-current sections (`Future Residents`, `Applicants`, `Pending Renewals`)
     and summary rows (`Totals`, `Summary`) immediately terminate active unit processing and
     emit `NON_CURRENT_SECTION_EXCLUDED` warnings.

## Unit & Charge Parsing Rules

- **Physical Inventory**:
  - Non-empty `Unit` with physical attributes defines a unit observation.
  - Physical unit identifiers remain internal for deduplication and contract-bound citations.
- **Charge Continuations**:
  - Blank-unit continuation rows (or repeating-unit continuation rows where unit type / sq ft
    are blank and charge/amount is populated) attach to the active physical unit's evidence list.
  - Continuation rows **never inflate physical unit counts**.
  - Emits `CONTINUATION_EVIDENCE` warning; orphan continuations without an active unit
    raise or emit `ORPHAN_CONTINUATION` blocker.
- **Charge Code Classification**:
  - Base rent codes (`RENT`, `BASE`, `MRENT`, `APTR`, etc.) are programmatically distinguished
    from ancillary charges.
  - Ancillary categories include `parking`, `pet`, `storage`, `utilities`, `washer_dryer`,
    and `concessions`.
  - Helper functions `classify_charge(code)`, `is_base_rent(code)`, and `is_ancillary_charge(code)`
    provide typed inspection.
- **PII Redaction**:
  - Tenant names are replaced with `[REDACTED]`.
  - Raw resident names, phone numbers, and canary strings never appear in serialized output.
- **Status Mapping**:
  - Current residents and Notice residents map to standard status `occupied`.
  - Vacant units map to standard status `vacant`.
  - Offline/down units map to standard status `down`.
  - Unrecognized or ambiguous statuses map to `None` with `UNSUPPORTED_STATUS` blocker.
- **Unresolved Use**:
  - Floorplans (e.g. `1BR`, `2BR`, `Studio`, `A1`) do **not** establish asset use (residential vs commercial).
  - Unless explicit use is stated (e.g. `Residential`, `Commercial`, `Apartment`, `Retail`),
    `unit_type` remains `None`.
  - Units with unresolved use are kept out of `residential_units`/`commercial_units` and
    recorded under `issues` with code `UNRESOLVED_UNIT_USE` containing the full unit `observation`.
  - When use is unresolved, overall status is `blocked` and scoped counts remain `None`.

## Safety & Limits

- Maximum input payload: 32 MiB (`33,554,432` bytes).
- Maximum worksheet rows: 50,000.
- Maximum worksheet columns: 128.
- Maximum worksheet cells: 250,000.
- Zip structure check rejects encrypted entries, VBA macros (`vbaProject.bin`), and external links.
- Uses `openpyxl` with `read_only=True`, `data_only=False`, `keep_links=False`.
- Library warnings are caught and sanitized as structural failures.
- Sanitized exceptions: only `RentRollNormalizationError(code)` is raised with closed codes:
  `MALFORMED_INPUT`, `INPUT_LIMIT_EXCEEDED`, `UNSUPPORTED_XLSX_FORMAT`, `EMPTY_INPUT`,
  `UNSUPPORTED_XLSX_LAYOUT`, `XLSX_DEPENDENCY_MISSING`.

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
      "citation": {"source_sha256": "...", "sheet": 1, "row": 8, "row_end": 8, "column": 2},
      "observation": {
        "unit_id": "101",
        "status": "occupied",
        "unit_type": null,
        "tenant_name": "[REDACTED]",
        "evidence": [...]
      }
    }
  ],
  "status": "blocked"
}
```

When all units have resolved use (e.g. explicit `Residential`), `residential_units` is populated,
`counts["residential"]` reflects observed totals, and `status` becomes `normalized_unvalidated`.
Output projects directly into the v2 observation contract (`validate_observations`).
