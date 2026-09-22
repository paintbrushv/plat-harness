# OneSite detailed BIFF XLS adapter

`plat_harness.ingest.onesite.normalize_onesite_xls(raw: bytes) -> dict`

This is a read-only, source-grounded **blocked-intake observation adapter**,
not an occupancy certifier. It does not call an engine or compute finances,
rents, occupancy percentages, forecasts, or underwriting metrics.

## Supported source shape

One OLE/BIFF workbook worksheet, independently tested with `xlrd 2.0.2`:

- A2 contains the OneSite brand; M3 is `Rent Roll Detail`.
- Row 9: A `Unit`, C `Floorplan`, H `Unit Designation`, R `Unit/Lease Status`,
  T `Name`, AJ `Trans Code`, AY `Total Billing`. Embedded line breaks are
  normalized. The original cells, not a converted CSV/XLSX, are cited.
- `Details` and repeated detail headers may occur between physical unit rows.
- Nonblank A identifies a physical row. Unit identifiers remain internal and
  are **redacted** in all returned observations. Floorplans/designations and
  resident/charge text are not returned.
- Source-proven states: `Occupied`, `Occupied-NTV`, `Occupied-NTVL` map to
  observed occupied; `Vacant`, `Vacant-Leased` map to observed vacant.
- Charge/co-tenant continuations without a physical identifier do not create
  units. Applicant, Pending, Pending Renewal, Future and Former lease rows
  are excluded; their continuations are not attached to current-unit evidence.
  Explicit future/former/applicant/pending-renewal sections are excluded.
- Identical repeated physical IDs add evidence, never inventory. Conflicting
  statuses block completeness. Unknown statuses, unidentified secondary
  statuses, orphan continuations and missing inventory boundaries block
  row-derived completeness instead of guessing.
- A `Totals:` row terminates inventory. The later B `Unit Status` / U `# Units`
  table provides explicit vendor count categories. `Units` is also accepted
  for a synthetic spelling variant. Only count-column U is read as counts;
  neighboring money columns are not used.

The compact G6-status layout and multi-sheet workbooks are unsupported.
Branding alone is not evidence of a supported layout. Unsupported inputs raise
`RentRollNormalizationError` with static codes, not source text.

## Contract and uncertainty

Root keys match `normalize_rent_roll`: `version`, `pms_type`, `source_sha256`,
`residential_units`, `commercial_units`, `counts`, `summary`, `issues`, `status`.
The PMS is `realpage`. Citations use original-byte SHA256, 1-based worksheet
position, row/row_end and column; worksheet names are never exposed.

This layout does **not** establish residential versus commercial use.
`Unit Designation` and floorplan codes are not use classifications. Therefore:

- Both scoped unit arrays remain empty; **all four counts in both scopes are
  null**, never inferred zero.
- `issues` entries with code `UNRESOLVED_UNIT_USE` retain a redacted physical
  unit in `observation`: null `unit_type`, supported observed `status` or null,
  redacted unit/resident identifiers, positional evidence.
- `issues` entry `ONESITE_REPORT_SUMMARY` carries an **unscoped** `observation`:
  `row_derived_counts`, `reported_counts`, `vendor_status_counts`, `citations`,
  `matched_fields`, and summary `status`. These are not canonical occupancy
  counts or authorized inputs to an engine.
- The vendor category `Admin/Down` is retained as `admin_down`, **not Down**.
  Even explicit `Admin/Down = 0` does not supply independently defined Down
  evidence. Model/Admin/NonRevenue are not mapped to Down either.
- Summary status is `absent` without a supported table, `unresolved` for
  incomplete/ambiguous evidence, or `mismatch` for conflicting comparable
  counts/internal vendor count totals. Occupied/vacant/total may match while
  Down and use remain unresolved. Such a result is **not `reconciled`**.
- Scoped summaries remain `absent`, since no scoped summary was supplied.
  This adapter always returns `status = blocked`; even a partial three-field
  match is never approved occupancy.

Consumers must not promote warning observations, matched fields, vendor totals,
or the number of returned redacted observations into approved occupancy.

## Reader boundaries and integration

- `xlrd` is lazy/optional. Missing reader: `XLS_DEPENDENCY_MISSING`.
- Require original XLS OLE magic, at most 8 MiB, one worksheet, 50,000 rows,
  128 columns and 250,000 dimension cells. Limit/layout failures are explicit.
- Read only; xlrd does not execute macros, formula programs, or external links.
  Cached formula values are not separately certified by this adapter.
- Warnings become sanitized structural failures; xlrd's diagnostic logfile is
  discarded. Exceptions are re-raised **outside** handlers to avoid retaining
  input-bearing `__context__`/`__cause__` chains.
- The public `normalize_rent_roll(stream, "realpage")` dispatch routes original
  BIFF bytes here; the `ingest` extra declares optional `xlrd`. CSV/XLSX
  continue through the separate flat-layout parser.
- No xlwt dependency is required to run tests or use this module.

## Synthetic tests and real-file validation

`tests/test_pms_onesite.py` embeds compressed/base64 **synthetic BIFF8** bytes,
not XLSX bytes renamed to XLS. The positive path invokes real xlrd. Fake-book
mutations exercise malformed/unsupported input and privacy boundaries.

Fixture generation recipe: use xlwt 1.3.0, create one sheet named
`SYNTHETIC PII SHEET`, write the nonempty cell values returned by
`fixture_rows()` to the same zero-based coordinates, save to `BytesIO`, then
`base64.b64encode(zlib.compress(buffer.getvalue()))`. The fixture contains
only synthetic resident canaries, units 101–103, a synthetic floorplan and
unit designation, fake charges/numbers and the structural labels above.
It reconstructs the **structure**, never copies any real rows.

Private campaign validation read seven original XLS files and checked hashes
against the source-only audit index: three detailed reports parsed with
blockers; four compact reports refused. Each detailed report retained redacted
physical-unit observations and matched occupied/vacant/total against explicit
unscoped vendor counts. Deal-specific counts stay in private evidence. These
are **observations, not approved occupancy**.
All four scoped counts stayed null, Down/use stayed unresolved, and no report
was certified. Per-report hashes, cited observations, status/exclusion counts,
coordinate read-back checks, red/green logs and remaining blockers live in the
private campaign evidence directory, never as private fixtures in this repo.
