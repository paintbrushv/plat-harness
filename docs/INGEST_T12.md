# T12 source-accounting preview: `ingest-t12-preview/1.0.0`

**Task 3.2.** A deterministic trailing operating-statement **preview**, not an
underwriting input and not a certification. The module turns a host-supplied
text grid (already extracted cells) into cited line-item money observations
with explicit period completeness and approved account-mapping references.
It performs **zero financial math** — no summing, totals, annualization,
rates, NOI, DSCR or effective rent — and makes no file, model or engine
calls. Every money observation reuses the frozen `ingest-accounting/1.0.0`
contract (`plat_harness.ingest.accounting`): finite decimal **strings** with
currency, unit and period plus a source locator; Python floats are refused;
missing amounts are null, never zero.

## The non-negotiable rule

Existing reconciliation annualization (the recon annualizer accepts partial
month coverage) is **NOT permission to treat a partial statement as a T12**.
A statement with fewer than twelve covered months previews with coverage
status `partial`, a `PARTIAL_MONTH_COVERAGE` **blocker**, and output status
`blocked`. The module contains no annualize helper of any kind.

## API

```python
from plat_harness.ingest.t12_normalizer import (
    CONTRACT_VERSION, ADAPTER, CANONICAL_ACCOUNTS, ERROR_CODES, ISSUE_CODES,
    T12PreviewError, preview_t12, validate_t12_preview, accounting_envelope,
    canonical_bytes,
)

result = preview_t12(
    grid=rows_of_text_cells,          # list[list[str|None]]; no file reads here
    first_row=4, first_column=2,      # physical one-based position of grid[0][0]
    sheet=1,                          # physical one-based sheet index
    source=source_record,             # {'source_id','sha256','role':'original',...}
    subject_id=host_subject, as_of=host_date,
    window={'first_month': '2025-09', 'last_month': '2026-08'},  # <= 12 months
    header_row=0, label_column=0,     # zero-based grid offsets
    mapping=approved_entries,         # tuple of {source_label, canonical_account,
                                      #  mapping_ref} records
    column_roles=None,                # optional {offset: 'descriptor'|'excluded'}
    cell_origin='typed',
)
checked = validate_t12_preview(result, subject_id=host_subject, as_of=host_date)
encoded = canonical_bytes(result, subject_id=host_subject, as_of=host_date)
envelope = accounting_envelope(result, subject_id=host_subject, as_of=host_date)
```

`preview_t12` validates the preview; `validate_t12_preview` re-derives every
month, coverage, row kind, observation and issue from the input and refuses
any tampering; `canonical_bytes` validates first, then emits compact
sorted-key UTF-8 JSON with no newline; `accounting_envelope` validates the
preview and emits an `ingest-accounting/1.0.0` envelope (flattened monthly
observations plus preview issues) that the frozen accounting contract
accepts.

Errors are `T12PreviewError` with static `.code`, message
`T12 preview refused (<CODE>).`, no chained decoder exceptions, and no input
values in `.args`. Refusal codes: `INVALID_INPUT`, `INVALID_MAPPING`,
`INVALID_WINDOW`, `SCOPE_MISMATCH`, `DUPLICATE_MONTH`, `MONTH_OUTSIDE_WINDOW`,
`EXCLUDED_MONTH_COLUMN`, `NO_MONTH_COLUMNS`, `EMPTY_STATEMENT`,
`AMBIGUOUS_NUMERIC_FORMAT`, `FORMULA_CACHE_NOT_ORIGINAL`.

## Revenue-quality evidence rules (all RED-tested)

- **Duplicate months refuse.** Two columns resolving to the same `YYYY-MM`
  refuse with `DUPLICATE_MONTH`; there is no silent dedupe-to-first.
- **Partial coverage blocks.** Months actually present are listed under
  `coverage.present_months`; months of the declared window that are absent
  are named in `coverage.missing_months`; a `PARTIAL_MONTH_COVERAGE` blocker
  cites the first month column. Fewer than twelve present months can never
  yield status `observed_unvalidated`.
- **Cumulative/YTD columns are never monthly figures.** Headers matching
  YTD / year-to-date / total(s) / cumulative / annual / yearly get column
  role `cumulative`; their cells are never cited as monthly observations.
- **Totals/subtotals never re-enter line items.** Labels starting with
  TOTAL / SUBTOTAL / NET OPERATING INCOME / NET INCOME / EFFECTIVE GROSS
  INCOME are row kind `total`: preserved verbatim as source-stated evidence
  with `mapping_state='not_applicable'`, never mapped, never summed into
  line items, and never cross-checked against them here.
- **Negatives/credits preserved as-is.** `(890.00)` stays
  `source_text='(890.00)'`, `decimal='-890.00'`. No sign normalization,
  absolute values or reclassification.
- **Unusual account columns become explicit issues, never dropped.** A
  non-month column carrying data with an unrecognized header gets role
  `unrecognized` and an `UNRECOGNIZED_COLUMN` issue — **blocker** when any
  data cell is present, warning when the header exists but the column is
  empty. The host may pre-clear a column with `column_roles={offset:
  'descriptor'}` (e.g. GL account codes) or `excluded` (e.g. a month outside
  the window); anything else stays visible.
- **Employee benefits vs property insurance stay distinct.** Separate
  canonical accounts (`employee_benefits`, `property_insurance`) with
  separate approved mapping references; no collapsing into one insurance
  or personnel line.
- **Commercial income is separate** from residential rental income, and
  **recurring ancillary income** (e.g. parking) is separate from
  **transactional ancillary income** (e.g. application fees).
- **Unsupported categories block, never omit.** An account outside
  `CANONICAL_ACCOUNTS` (e.g. `interest_expense`, `depreciation` — financing
  and non-cash items) refuses the mapping with `INVALID_MAPPING`; if such a
  row appears in the statement it becomes an unmapped line item with an
  `UNMAPPED_ACCOUNT` **blocker**, with every monthly observation preserved.
- **Unmapped material account blocks.** A line item with no approved
  mapping emits `UNMAPPED_ACCOUNT` — **blocker** when any cited month is a
  nonzero amount, explicit **warning** when every month is an explicit typed
  zero. Unmapped rows keep all monthly observations; nothing is dropped.
- **Unlabeled numeric rows block.** A row with month values but no account
  label becomes row kind `unlabeled` with a `UNLABELED_ROW` blocker; the
  values are preserved, never dropped or attributed to a neighbor.
- **Missing month cells are null, never zero.** An empty/`n/a`/dash cell
  yields `amount: null` plus a `MISSING_MONTH_CELL` blocker naming the
  row; the frozen accounting contract keeps the envelope `blocked`.
- **No invented or fuzzy mappings.** Matching is exact on the source label.
  `parking income`, `Parking Income Extra` and `Parking Income ` (trailing
  space) do not match an approved `Parking Income` mapping; each becomes its
  own unmapped blocker. Mapping references are opaque
  `map_[0-9a-f]{64}` IDs supplied by the host approval process; the module
  invents none.
- **Period completeness is explicit.** `months` lists every month column
  actually found (key, column, header); `coverage` carries
  `status`/`expected_months`/`present_months`/`missing_months`;
  `census` counts `preceding` rows above the header, the `header` itself,
  and each row kind — nothing above the header row is silently skipped.

## Row kinds

| Kind | Meaning |
|---|---|
| `section` | Labeled row with no month values and no approved mapping (e.g. `INCOME`) — carried, no amounts |
| `blank` | No label and no month values |
| `line_item` | Labeled account row (mapped or unmapped) |
| `total` | Total/subtotal/NOI/net-income/EGI label — evidence only |
| `unlabeled` | Month values with no label — blocker |

## Canonical accounts

`residential_rental_income`, `commercial_rental_income`, `other_income`,
`ancillary_income_recurring`, `ancillary_income_transactional`,
`property_taxes`, `property_insurance`, `employee_benefits`, `payroll`,
`utilities`, `repairs_and_maintenance`, `marketing`, `administrative`,
`management_fees`, `legal_professional`, `turnover`. Financing items
(interest expense), non-cash items (depreciation) and any other category are
outside this surface and refuse as mappings.

## Month headers

Recognized forms: `Sep 2025` / `SEP 2025` / `sept 2025` / `September 2025`
(case-insensitive, space or hyphen), `2025-09`, `2025-09-01`,
`9/1/2025` (US month/day). Everything else — `Budget`, `FY 2025`, `2025`,
`Sep`, `Q3`, `2025-9`, `Total` — is not a month. Headers that resolve to a
month outside the declared window refuse with `MONTH_OUTSIDE_WINDOW` unless
the host marks the column `excluded`. A statement with no month columns
refuses with `NO_MONTH_COLUMNS`.

## Trust boundary

This preview establishes **syntax, internal consistency and declared
source/scope binding** only. Passing validation does not authenticate a
workbook, approve an account mapping, annualize anything, or certify
economics. Mapping references are opaque IDs whose approval lives entirely
outside this module. Status `observed_unvalidated` means exactly that.
The underwriting engine, reconciliation and any annualization remain
separate owners; this module is the input gate, consistent with the frozen
evidence validation in the slice-B worker.

Synthetic tests in `tests/test_t12_normalizer.py` construct in-memory grids
only. No live or private deal files are read, and no engine or model is
invoked.