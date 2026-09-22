# Canonical engine-intake translation: `ingest-canonical-intake/1.0.0`

**Task 3.3.** A thin deterministic adapter translating already-validated
harness evidence into the engine's canonical deal shape
(engine-owned `deal_schema_v0_1.json`; see
`docs/canonical_deal_schema_v0_1.md in the sibling engine repository`),
plus a separate source-lineage sidecar. It is **not** an engine invocation, a
parser, an approval authority or a reconciliation. No file reads, model calls,
engine execution, engine imports or network access occur in this module; the
engine repository is read-only and is never touched.

## API

```python
from plat_harness.adapters.canonical_intake import (
    CONTRACT_VERSION, ADAPTER, ENGINE_NUMERIC_PATH, ENGINE_SCHEMA_VERSION,
    ERROR_CODES, BLOCKER_CODES, SUPPORTED_LEASE_BASIS,
    CanonicalIntakeError, translate_intake, canonical_bytes,
)

result = translate_intake(
    evidence,                       # exact intake contract below
    subject_id=host_subject,        # must equal every nested envelope scope
    as_of=host_date,                # ditto
    policy_version=host_policy,     # must equal evidence policy_version
)
```

`translate_intake` returns **exactly**:

| Key | Meaning |
|---|---|
| `contract_version` | `ingest-canonical-intake/1.0.0` |
| `status` | `eligible` or `blocked` |
| `subject_id` / `as_of` / `policy_version` | echoed host scope |
| `canonical` | engine-shaped dict (`None` when blocked) |
| `canonical_json` | compact sorted-key UTF-8 JSON of `canonical` (`None` when blocked) |
| `canonical_sha256` | SHA256 of `canonical_json` bytes (`None` when blocked) |
| `draft` | engine-shaped dict with explicit nulls naming what is missing (`None` when eligible) |
| `blockers` | list of `{code, field, detail}` entries; `field` is a JSON-pointer-style path or section name |
| `lineage` | separate source-lineage sidecar (below) |

## Result contract

**Eligible** — every gate passed. `canonical` keys stay inside the engine
schema's `additionalProperties: false` object key sets; money values are exact
finite decimal **strings** certified upstream by `ingest-accounting/1.0.0`
(reused by import from `plat_harness.ingest.accounting`, never forked).
`canonical_json` is deterministic: sorted keys, compact separators,
`ensure_ascii=False`, no newline. Identical evidence produces byte-identical
JSON and an identical SHA256 regardless of which provider/model labels
produced the evidence.

**Blocked** — a gate failed. `canonical`, `canonical_json` and
`canonical_sha256` are `None`; `draft` carries the engine-shaped skeleton with
an explicit `null` at each missing field; `blockers` names exactly what is
missing with a closed static code set. Nothing is zero-filled, fabricated or
estimated, and no engine call is ever made.

**Refused** — `CanonicalIntakeError` with a static `.code` and message
`Canonical intake refused (<code>).`. Codes: `INVALID_INPUT`,
`SCOPE_MISMATCH`, `UNSUPPORTED_LEASE_BASIS`, `UNAUTHORIZED_TARGET_RENT`,
`AMBIGUOUS_NUMERIC_FORMAT`. No input values, paths or chained decoder
exceptions are retained in `.args`, `.__cause__` or `.__context__`.

`canonical_bytes(evidence, subject_id=..., as_of=..., policy_version=...)`
validates first and returns eligible canonical JSON bytes, refusing otherwise.

## Zero financial math

The adapter performs **no** financial arithmetic: no summing, no
annualization, no rate computation, no rounding, no `float`, no `Decimal`.
Money values are the exact decimal strings already certified by the frozen
accounting contract; a cited null amount or missing citation is a blocker,
never a zero. If a schema-required transformation to native engine numbers is
ever needed, it belongs on the deterministic engine-owned path
`engine.modules.util.dec` (`ENGINE_NUMERIC_PATH` documents the reference);
this module never imports, copies or re-implements that arithmetic.

Numeric text that is not a plain finite decimal string (thousands separators,
percent suffixes, hex, stray spaces, `NaN`-like tokens) refuses with
`AMBIGUOUS_NUMERIC_FORMAT` — the frozen accounting rules are applied **by
import** from `plat_harness.ingest.accounting`, not re-implemented here.

## Gates

| Gate | Effect |
|---|---|
| `subject_id` / `as_of` / `policy_version` mismatch (top level, nested envelope or nested source record) | refuse `SCOPE_MISMATCH` |
| Unsupported `lease_basis` (only `monthly_rent` is supported) or a cited unit-rent observation with a non-monthly period | refuse `UNSUPPORTED_LEASE_BASIS` |
| `target_rent` present without an exact well-formed authorization `{authorized: true, authority_ref}` | refuse `UNAUTHORIZED_TARGET_RENT` (fail-closed) |
| Ambiguous numeric text in rates or millage | refuse `AMBIGUOUS_NUMERIC_FORMAT` |
| Any other shape violation | refuse `INVALID_INPUT` |
| Incomplete / absent / reversed analysis window | block (`ANALYSIS_WINDOW_INCOMPLETE`) |
| Null residential occupancy counts or unresolved unit use | block (`OCCUPANCY_COUNTS_INCOMPLETE` / `UNRESOLVED_UNIT_USE`) |
| Missing millage | block (`MILLAGE_MISSING`) |
| Missing / null-amount / uncited in-place or market rent | block (`INPLACE_RENT_MISSING` / `MARKET_RENT_MISSING`) |
| Unexplained commercial or other-income omission | block (`COMMERCIAL_INCOME_OMISSION_UNEXPLAINED` / `OTHER_INCOME_OMISSION_UNEXPLAINED`) |
| Cited present income with a null amount | block (`MISSING_INPUT` at `/revenue_programs[0]/price_value`) |
| Revenue programs present without Programs/ALL collection-loss coverage | block (`MISSING_INPUT` at `/collection_loss_curve`) |
| Missing metadata (`run_id`, `analyst`, `purpose`), absent occupancy/accounting/cohorts/curves sections | block (`MISSING_INPUT`, naming the exact field) |

Occupancy and accounting envelopes are re-validated **by import** through the
frozen `ingest-observation/2.0.0` and `ingest-accounting/1.0.0` validators;
their refusal codes surface as `SCOPE_MISMATCH` / `AMBIGUOUS_NUMERIC_FORMAT` /
`INVALID_INPUT` here.

## Lineage sidecar

`result['lineage']` is a **separate structure**; strict engine objects never
carry arbitrary provenance keys (`provider_id`, `model_id`, `lineage`,
`policy_version`, `* _provenance` are absent from every canonical object):

| Key | Meaning |
|---|---|
| `contract_version` | `ingest-canonical-intake/1.0.0` |
| `status` | mirrors result status |
| `canonical_sha256` | ties the sidecar to exact canonical bytes (`None` when blocked) |
| `subject_id` / `as_of` / `policy_version` | host scope |
| `provider` | the provider/model labels that produced the evidence |
| `evidence_sha256` | per-envelope canonical SHA256 of the validated occupancy and accounting envelopes (`None` where the section is absent) |
| `field_lineage` | list of `{canonical_path, observation_id, source_id, source_sha256, decimal}` entries binding canonical money fields to their cited observations |
| `omission_explanations` | the recorded explanations for `commercial_income` / `other_income` omissions (`None` when the section is absent) |

Because provider metadata lives only in the sidecar, two runs of identical
evidence under different provider labels produce byte-identical canonical JSON
and an identical `canonical_sha256`; only `lineage['provider']` differs.

## Trust boundary

Passing translation is **not** underwriting authority, a certification, or an
engine invocation. It certifies only that validated harness evidence has been
shaped into the engine's canonical contract. Execution authority, approval
registries and engine runs belong to the separately reviewed execution
contract (Task 3.4), never to this adapter.

## Known limits

- Only `monthly_rent` lease basis is supported; other bases refuse rather than
  convert (no annualization math exists here by design).
- Cohort translation supports the intake cohort shape used by the harness
  evidence envelope: `cohort_id`, `unit_type`, `unit_count`,
  `inplace_rent_observation_id`, `market_rent_observation_id`, optional
  `target_rent`. Engine-only cohort fields (sqft, bedrooms, bathrooms) are not
  synthesized.
- Rates are carried as decimal strings per the money-preservation rule;
  converting to engine-native numbers is the engine-owned path's job
  (`ENGINE_NUMERIC_PATH`), not this adapter's.
- Missing `metadata.market` / `address` / `year_built` are not fabricated; the
  engine treats those metadata fields as optional and they are simply absent.
- Revenue programs and adoption curves are translated only from cited
  commercial/other-income evidence with an exact program contract; nothing is
  invented from uncited lines.