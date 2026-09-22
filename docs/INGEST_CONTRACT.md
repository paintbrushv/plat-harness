# Ingestion observation contract: `ingest-observation/2.0.0`

**Task 1.1 proposal for parent interface review.** This is a separate, pure v2
entrypoint, not a replacement for `normalize_rent_roll(...)->version=1.0`.
Neither the v1 parsers, legacy occupancy helper nor host approval contracts are
modified. No file reads, model calls, dependencies, financial results, approval
registry, human decisions or execution authority are introduced here.

## API

```python
from plat_harness.ingest.contracts import (
    CONTRACT_VERSION, ObservationContractError,
    validate_observations, loads_observations, canonical_bytes, observation_id,
)

validated = validate_observations(value, subject_id=host_subject, as_of=host_date)
validated = loads_observations(utf8_bytes_or_str, subject_id=host_subject, as_of=host_date)
encoded = canonical_bytes(value, subject_id=host_subject, as_of=host_date)
unit_key = observation_id(first_unit_id_citation)
```

The first two return defensive copies of JSON-native dictionaries. Validation
never fills nulls, derives missing values into the returned payload, repairs
conflicts, removes observations, or upgrades status. Canonical serialization
validates first, sorts **object keys**, preserves list order/multiplicity, uses
compact separators and UTF-8 with `ensure_ascii=False`, and adds no newline.
Its bytes can be hashed using standard SHA256. This is a specified Python JSON
encoding, not a claim of RFC 8785/JCS compatibility.

Errors are `ObservationContractError` with static `.code` and message:
`INVALID_OBSERVATIONS`, `INPUT_LIMIT_EXCEEDED`, or `SCOPE_MISMATCH`. No input
values, dynamic paths or chained decoder/validation exceptions are retained in
`.args`, `.__cause__`, or `.__context__`. Applications must still avoid logging
raw arguments or traceback **locals**. This validator is not a PII scrubber.

## Trust boundary

Validation establishes **syntax, internal consistency and declared source/scope
binding**, not source truth, actual locator existence, semantic interpretation,
redaction completeness or source authority. The caller must supply host-owned
opaque IDs, adapter identity/version and subject/date, and deterministically
extracted observations. Model-authored counts are not an accepted ingestion
workflow; JSON validation alone cannot identify who produced a number.

A source resolver must separately read host-authorized original bytes, verify
their SHA256 and the cited physical positions/values. No identifier or citation
is a filesystem path or file-access permit. An attacker can invent internally
consistent source metadata; passing this validator does not authenticate it.
Raw source paths, original unit/building keys, tenant identities and private
source-to-path maps stay outside the envelope. Host identity tokens must not be
populated from document text.

`coverage="established"` is a **cited source-scope assertion**, never an approval.
The validator cannot determine whether the cited cell really establishes that
scope. Host-reviewed mapping and reconciliation, original-byte verification,
canonical financial inputs and execution authority remain separate later gates.
No `approved`, `certified`, confidence, financial amounts, rates, notes, arbitrary
metadata, generic issue payloads or model-generated financial fields exist.
Money will require a separately versioned extension with finite decimal strings,
explicit currency/unit/period and locators; floats are forbidden in this schema.

## Exact envelope

Every field below is required unless its **value** is explicitly nullable.
Every object rejects extra keys. Only ordinary JSON-native dictionaries, lists,
strings, integers, booleans (none are permitted by a final field), and null can
reach shape validation. Python subclasses, tuples, decimals, floats and arbitrary
objects are refused.

| Field | Shape / meaning |
|---|---|
| `contract_version` | Exactly `ingest-observation/2.0.0` |
| `subject_id` | Host-owned token `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, or null |
| `as_of` | Real calendar date, exactly `YYYY-MM-DD`, or null; no timezone coercion |
| `adapter` | Exactly `{id, version}`; ID `[a-z][a-z0-9_-]{0,63}`, version three nonnegative decimal components, each at most six digits with no leading zeros |
| `sources` | Nonempty list of at most 128 source records |
| `units` | List of observations with explicit residential/commercial use |
| `unknown_use_units` | Separate list of observations whose `unit_type` is null |
| `summaries` | List of source summaries, including absent/invalid observations |
| `issues` | List of static, optionally position-cited issues with typed links |
| `completeness` | Exactly `{residential, commercial}`, each described below |
| `status` | `blocked` or `observed_unvalidated`; neither means approved |

Both host keyword arguments are mandatory and must exactly equal the envelope
and every source record. Null is accepted only when the corresponding host
argument is also null; it is never a wildcard for a known subject/date. Source
records cannot mix dates or subjects in one envelope.

### Source records and identity

Each source is exactly:

```text
{source_id, sha256, role, original_source_ids, subject_id, as_of}
```

- `source_id`: `src_` plus 32 lowercase hexadecimal digits, unique in the envelope.
- `sha256`: 64 lowercase hexadecimal digits for those exact source bytes. Digest
  aliases are rejected: the same bytes cannot be declared again under another ID.
- `role`: `original` or `derivative`. Original records have an empty
  `original_source_ids`; derivatives have a nonempty, unique list referencing
  declared **originals**, not other derivatives. Missing parents, self-links and
  cycles therefore refuse. Flatten verified original ancestry before use.
- Derivative bytes are not promoted to originals. Observations on a derivative
  must carry citations into its declared originals, never generated CSV cells
  bound only to a derivative digest. If original lineage is unavailable, stop
  migration rather than relabeling generated bytes as originals.

`sum_` and `iss_` plus 32 lowercase hex digits identify summaries and issues,
respectively; each is unique in its collection. These are host/adapter-minted,
not source text. Suggested deterministic migration IDs are a domain-separated
hash of source identity and the source-order record position, truncated to 32
hex characters; validation requires uniqueness, not that suggested algorithm.

### Exact citations

Inline citations have one of two exact forms, with no text sheet names:

```text
{source_id, source_sha256, sheet, row, row_end, column}
{source_id, source_sha256, page, bounds}
```

`source_id` must name an original and `source_sha256` must equal its declared
hash. Observation/summary citations must belong to that record's source or its
original ancestry. A cited issue's linked records must share that ancestry.
Null issue citations are allowed, including on linked issues when preserving a
v1 null citation. Typed links identify observations/summaries but do not supply
missing issue evidence; a null citation confers no evidence.

- Sheet/row/column positions are physical, one-based integers, **not booleans**.
  `sheet` is 1..1024, `row <= row_end` is 1..1,000,000, and `column` is 1..16,384.
  CSV sheet position is 1; quoted multiline CSV retains physical start/end lines.
  Workbook sheets are indexed in physical workbook order; do not rename sheets.
- `page` is the one-based original page, 1..100,000. `bounds` is exactly
  `[left, top, right, bottom]`, integer millionths of the original displayed page
  extent, origin at top-left: each value 0..1,000,000, left < right, top < bottom.
  Adapters must document page rotation/crop handling and deterministic conversion
  from native coordinates; a page-only or generated text-line locator is refused.
- These are resource bounds, not a promise that a particular parser supports
  those limits or that the source contains the claimed position.

### Unit observations and evidence

Each observation is exactly:

```text
{observation_id, source_id, unit_type, status, evidence}
```

`unit_type` is `residential`/`commercial` in `units`, and null in
`unknown_use_units`. `status` is `occupied`, `vacant`, `down`, or null. Admin,
Model, NonRevenue and combined Admin/Down are **not** aliases for Down.

`evidence` is a nonempty, ordered list of nonempty field-to-citation maps.
The exact allowlist preserves every current v1 evidence field:

```text
unit_id status unit_type tenant_name phone email record_type designation floorplan charge
```

These keys retain only **citations**, never the field's raw text or rent amount.
The first group must include `unit_id` and `status`; explicit-use units also need
a `unit_type` citation somewhere in the evidence. Continuation groups may contain
only `tenant_name`/`charge`, or any other permitted subset. Groups are not merged
or deduplicated, so repeated source evidence and all field citations survive.

`observation_id = "unit_" + SHA256(canonical JSON of evidence[0].unit_id)`.
Use `observation_id(anchor)` rather than hashing resident/unit strings. The
helper validates citation **syntax**; the envelope additionally checks binding.
IDs are unique across both unit collections. A physical `unit_id` locator
cannot belong to two different observations, even through continuation groups.
The first occurrence is the stable anchor; rearranging continuation groups does
not alter identity. This identifies an observation in one source version, not
a resident, cross-report unit master or cross-date identity. Cross-source
semantic deduplication is outside this contract.

### Counts and completeness

Each scope is exactly:

```text
{enumeration, coverage, coverage_citations, counts}
```

`counts` always has exactly `occupied`, `vacant`, `down`, `total`. Each value is
null or an integer 0..1,000,000. Booleans, decimal strings, integral/fractional
floats, nonfinite values and negative values are rejected without coercion.

The v2 disjoint definition is **one retained physical-unit observation, one
status category, one use scope**. Occupied includes explicitly mapped current or
notice occupancy; Vacant excludes Down; Down requires independently supported
out-of-service classification. This contract validates the resulting enum,
not the source semantics of those mappings. Every nonnull enumeration count
must equal its deterministic count over the matching retained `units`; total
is their length. If any selected status is unknown, category counts must stay
null (total can remain known). Unknown-use observations never enter either
scope's counts. Fully nonnull counts must satisfy occupied + vacant + down = total.

- `enumeration="unknown"`: all counts are null.
- `enumeration="partial"`: some or all derived observation counts can be retained;
  they describe only enumerated records, not the asset population.
- `enumeration="complete"`: all four counts are nonnull and internally reconcile;
  unknown use anywhere prevents claiming a complete scoped enumeration.
- `coverage="unknown"`: `coverage_citations=[]`; no inference of absent inventory.
- `coverage="established"`: requires a nonempty list of original citations, known
  host subject/date, complete enumeration and no unknown-use observations.
  It is a separately supplied source-scope assertion, not an automatic result of
  successful enumeration or arithmetic equality.

**An empty commercial list can produce a zero row enumeration but does not
establish no commercial inventory.** A v1 scoped zero must not silently become
`coverage="established"`. A zero total never produces a rate: no rate field or
rate function exists here. Empty parses remain blocked, including empty
zero-count envelopes. No division or financial arithmetic is performed.

### Source summaries

Every summary is exactly:

```text
{summary_id, source_id, scope, status, reported_counts, row_derived_counts,
 vendor_status_counts, citations, matched_fields}
```

- `scope`: `residential`, `commercial`, or `unknown` (whole unscoped source report).
- `status`: `absent`, `unresolved`, `invalid`, `mismatch`, or `reconciled`.
- `reported_counts`: null or the strict four-key count object. These are source
  claims, not replacement enumeration counts. Contradictions remain representable
  under blocked `mismatch`/`invalid`/`unresolved` state; they are not repaired.
- `row_derived_counts`: null or the same count shape, preserving current OneSite
  partial enumeration. Nonnull entries must match observations for this source
  and scope (`unknown` selects all its observations). Unknown entries stay null.
- `vendor_status_counts`: a possibly partial map whose only keys are
  `occupied_no_ntv`, `occupied_ntv`, `occupied_ntv_leased`, `vacant_leased`,
  `admin_down`, `vacant_not_leased`, `totals`. Values are strict counts or null.
- `citations`: ordered list of nonempty field-to-citation maps. Keys are the four
  count fields or the seven vendor tags. Every supplied vendor tag needs a
  citation, including null/invalid observations. Citation keys whose source
  count was invalid may remain without a count value. All v1 flat citation groups
  survive; the v1 OneSite citation map becomes one group (or none when empty).
- Nonnull reported fields require direct field citations, or these deterministic
  vendor-group bindings: occupied = the three occupied tags; vacant = the two
  vacant tags; total = totals. Every contributing tag must be nonnull/cited.
  **There is no Admin/Down-to-Down binding, even for an Admin/Down count of zero.**
- `matched_fields`: unique list drawn from the four count keys. Each named field
  must be nonnull/equal in reported and row-derived counts; never invent matching
  fields or use matching total alone as reconciliation.
- `reconciled` additionally requires an explicit residential/commercial scope,
  full reported counts equal to source observations, disjoint equality, complete
  scoped enumeration and no unknown use. It is still not coverage or approval.
- `absent` requires null reported counts and empty vendor/citation/matched
  collections; row-derived counts can survive an absent vendor summary.

A summary mismatch, invalid or unresolved summary, unknown use/status, a blocker
issue, or no observed units requires root `status="blocked"`. Blocked envelopes
remain valid contract objects, not exceptions that discard useful evidence.

### Issues

Each issue is exactly:

```text
{issue_id, code, severity, citation, observation_ids, summary_ids}
```

`code` belongs to the closed `ISSUE_CODES` exported in the module. The tests
inventory both existing parser implementations to ensure their current issue
codes are all covered. New codes require a reviewed versioned contract change.
`severity` is `warning` or `blocker`; only existing warning codes in
`WARNING_CODES` may be warnings, so parser blockers cannot be downgraded. A host
may conservatively escalate a warning to a blocker.

`citation` is an original positional citation or null. Both reference lists are
unique and refer to existing typed records. `UNRESOLVED_UNIT_USE` requires
nonempty observation links to unknown-use units and no summary links.
`ONESITE_REPORT_SUMMARY` requires nonempty summary links and no unit links.
There is deliberately **no** generic `observation`, raw-value, reason or message
payload. Render explanatory text from a separately maintained static code table.

## v1 migration policy for Task 1.2 (not implemented here)

Do not redefine v1, infer scope/date, silently filter unknown keys or run a
round-trip through raw identifiers. Task 1.2 must test the actual parser outputs,
not only these v2 fixtures, with a preservation ledger before returning success.

| v1 input | Explicit v2 projection |
|---|---|
| `version=1.0` | New envelope version; old function/output remains unchanged |
| `pms_type` plus parser choice | Host-selected adapter ID/version must retain the original vendor label: `pms-flat-yardi`, `pms-flat-realpage`, `pms-flat-entrata`, or `onesite-detailed-realpage` |
| `source_sha256` | Declared original digest only when original-byte provenance is established; otherwise require original ancestry |
| source subject/date absent | Null envelope/source scope and null host arguments until independently established |
| scoped unit arrays | `units`, retaining status/use and every evidence group; raw unit key replaced by position-derived ID; redacted PII placeholders omitted, citations retained |
| `issues[].observation` unit | `unknown_use_units` once; issue gains `observation_ids` link; do not add it to `units` or count it twice |
| root scoped summaries | `summaries` records, including `absent`; preserve all reported counts/citation groups/status; new nullable fields null/empty, never fabricated |
| OneSite `ONESITE_REPORT_SUMMARY` observation | Summary with scope `unknown`; preserve **status, reported_counts, row_derived_counts, vendor_status_counts, citations, matched_fields**, and link original issue with `summary_ids` |
| original issues | Preserve every code, severity and citation, including null; assign distinct issue IDs in original order |
| scoped counts | Explicitly labelled enumeration only, with coverage unknown; nulls retained; empty-scope zeros are never source coverage |
| `normalized_unvalidated` | `observed_unvalidated` only if v2 consistency checks permit; `blocked` never upgrades |

Invalid source cells remain represented by their existing static issues and
citations rather than raw source text. The contract cannot recover observations
that the v1 parser itself never emitted. Unsupported future issue payloads or
unverifiable derivative lineage must fail explicitly, not be silently dropped.
Adapter naming/version decisions and the migration preservation ledger require
parent approval before downstream interface freeze.

## Limits

Input and canonical output: at most 8 MiB each. Native tree traversal: depth at
most 16, at most 500,000 nodes including dictionary keys, collection length at
most 50,000, strings/keys at most 128 code points, cumulative UTF-8 text at most
8 MiB. Sources have the tighter 128-entry bound. Counts/positions have their
field-specific limits. Effective record capacity can be lower when many evidence
groups consume the byte/node budget. Refuse rather than truncate.

The loader bounds encoded bytes and JSON nesting **before** decoding, rejects
duplicate keys at every level, rejects every float/exponent token and nonfinite
constant, and then applies the full native-tree/shape checks. The standard JSON
decoder can allocate up to the bounded input size before collection/node checks;
this is not a general hostile-document parser sandbox. Document parsing, worker
memory/time limits, source truth and authorization are outside this module.

## Minimal runnable envelope

Synthetic empty/unknown example, deliberately blocked. These digests and IDs
are placeholders, not evidence of a parsed real source.

```python
from plat_harness.ingest.contracts import validate_observations, canonical_bytes

example = {
    'contract_version': 'ingest-observation/2.0.0',
    'subject_id': None, 'as_of': None,
    'adapter': {'id': 'synthetic-flat', 'version': '1.0.0'},
    'sources': [{
        'source_id': 'src_' + '1' * 32, 'sha256': 'a' * 64,
        'role': 'original', 'original_source_ids': [],
        'subject_id': None, 'as_of': None,
    }],
    'units': [], 'unknown_use_units': [], 'summaries': [], 'issues': [],
    'completeness': {
        scope: {
            'enumeration': 'unknown', 'coverage': 'unknown',
            'coverage_citations': [],
            'counts': {'occupied': None, 'vacant': None, 'down': None, 'total': None},
        } for scope in ('residential', 'commercial')
    },
    'status': 'blocked',
}
assert validate_observations(example, subject_id=None, as_of=None) == example
assert canonical_bytes(example, subject_id=None, as_of=None)
```

`tests/test_ingest_contracts.py` also includes synthetic cited units, a disjoint
complete enumeration with independent coverage evidence, multiline/cell and page
locators, derivative ancestry, and an unresolved OneSite-shaped vendor summary.
Tests establish only engineering behavior, not real-export support, economic
correctness, approval or completed Item 1 reconciliation.
