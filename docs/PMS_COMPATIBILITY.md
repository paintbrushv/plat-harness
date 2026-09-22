# Public PMS compatibility matrix

`plat_harness.ingest.compatibility.public_matrix()` publishes a versioned
compatibility view (`pms-compatibility-matrix/1.0.0`). It is **not** vendor
certification, engine permission, or Item 2 milestone acceptance. Parent
verification and independent review are required before treating any row as a
real-export proof. Public fixtures are synthetic; this document does not read
or cite original deal-room files.

Engine eligibility is always `not_evaluated`. The Item 2 milestone field is
`not_evaluated`. Zero sources ran against originals is not success.

## Contract

Each reader row has exactly:

```
reader_id, reader_version, container, structural_signature,
provenance_confidence, supported_fields, source_test_status,
limitations, refusal_codes
```

Shared honest limits on every reader:

- `unknown_use` — floorplan or designation does not establish residential versus commercial use.
- `independent_down_unresolved` — Admin/Down or combined categories are not independent Down.
- `csv_only_semantic_resolver` — reconciliation still requires the frozen seven-column ASCII CSV resolver.
- `engine_eligibility_not_evaluated` — a parse is not engine permission.

`source_test_status` may be `synthetic_only` or `acquisition_gap`. Synthetic-only
rows cannot be labelled `real_export_verified`. Extra or missing keys refuse.
Totals are recomputed from rows; declared success counts are not trusted.

## Readers

| reader_id | container | structural signature | source_test_status | provenance_confidence |
|---|---|---|---|---|
| pms-flat-yardi | csv\|xlsx | flat_inventory_header | synthetic_only | synthetic_structural |
| pms-flat-realpage | csv\|xlsx | flat_inventory_header | synthetic_only | synthetic_structural |
| pms-flat-entrata | csv\|xlsx | flat_inventory_header | acquisition_gap | acquisition_gap |
| onesite-detailed-realpage | xls_biff | onesite_detailed_row9 | synthetic_only | synthetic_structural |
| lease-charge-xlsx | xlsx | split_header_lease_charge | synthetic_only | synthetic_structural |
| onesite-compact | xls_biff | onesite_compact_g6 | synthetic_only | synthetic_structural |
| pdf-rent-roll | pdf | pdf_split_header_current_notice_vacant | synthetic_only | synthetic_structural |

Supported observation fields are structural only: `unit_id`, `status`,
`unit_type`, `counts`, `summary`, `issues`. Tenant identifiers are redacted by
the readers and are not compatibility evidence.

Closed refusal codes are the existing adapter static codes (malformed input,
limits, missing optional spreadsheet/PDF dependencies, unsupported layout or
container). They are not source text.

## Acquisition gap: Entrata originals

Missing Entrata originals are an **acquisition gap**, not fabricated coverage.
The Entrata flat reader remains listed so the gap is visible. Coverage accounting
records `entrata-originals` as accounted-with-status `acquisition_gap`. An omitted
declared original fails coverage accounting. No synthetic dialect may be relabelled
as a verified vendor export.

## Two-archetype gate (blocked)

Intended pair:

1. Abilene garden (lease-charge-xlsx structural reader)
2. 360 Market Square high-rise (onesite-compact structural reader)

Gate status is **blocked**. Independently reviewed use evidence is absent.
Independent Down evidence is absent. Four-count reconciliation is blocked on
both rows. Engineering tests on synthetic bytes do not close this gate.

Do not invent a four-count pass by narrowing the manifest. Student Housing is
unverified; an unknown subtype cannot be relabelled Student Housing.

## Public sanitize

This public view must not contain private paths, overlay directory names, or
mailbox identifiers. Intended-pair labels above are scope names for the gate,
not filenames and not a claim that original bytes were read for this matrix.
