# Original-byte-backed v1 migration

Task 1.2 adds a separate, pure entrypoint in `plat_harness.ingest.compat`.
`normalize_rent_roll(stream, pms_type)` and its `version="1.0"` output remain
unchanged. The existing v2 contract and approval contracts are unchanged.
See [INGEST_CONTRACT.md](INGEST_CONTRACT.md) for the exact v2 schema.

## API and runnable synthetic example

Both APIs require all host keyword arguments, including explicitly nullable
`subject_id` and `as_of`. `migrate_v1` returns a defensive v2 dictionary.
`verify_preservation` independently checks an existing v2 artifact and returns
an aggregate preservation ledger; it does not embed that ledger in the envelope.

```python
import io
from plat_harness.ingest import normalize_rent_roll
from plat_harness.ingest.compat import migrate_v1, verify_preservation
from plat_harness.ingest.contracts import canonical_bytes

# Public synthetic source, not evidence of real vendor support or source scope.
original_bytes = b"Unit,Status,Unit Type\n101,Current,Residential\n"
v1 = normalize_rent_roll(io.BytesIO(original_bytes), "yardi")
host = {
    "source_id": "src_" + "1" * 32,
    "subject_id": None,
    "as_of": None,
    "adapter_id": "pms-flat-yardi",
    "original_bytes": original_bytes,
}
v2 = migrate_v1(v1, **host)
ledger = verify_preservation(v1, v2, **host)
encoded = canonical_bytes(v2, subject_id=None, as_of=None)
assert ledger["retained_v1_sha256"] == ledger["reconstructed_v1_sha256"]
assert v2["completeness"]["commercial"]["coverage"] == "unknown"
assert v2["summaries"][0]["row_derived_counts"] is None
```

Signatures:

- `migrate_v1(value, *, source_id, subject_id, as_of, adapter_id, original_bytes) -> dict`
- `verify_preservation(value, migrated, *, source_id, subject_id, as_of, adapter_id, original_bytes) -> dict`

`value` must be the exact JSON-native **actual parser output** for those bytes
and that adapter. Object key ordering is immaterial; list ordering, multiplicity,
nulls, field presence and native types are not. No v1 field accepts booleans,
including booleans that compare equal to integers in Python. JSON text, arbitrary
objects, subclasses and claims edited after parsing are rejected. Callers must
not mutate inputs concurrently during a call.

The immutable native `bytes` are hashed and reparsed by the corresponding
unchanged normalizer. Canonical native-tree comparison rejects fabricated or
modified v1 counts, identifiers, citations, issue payloads and vendor labels.
The code does not trust a supplied digest in place of actually parsing the bytes.
It neither reads a pathname nor resolves an artifact ID from disk.

## Host authority and derivative limitation

**The host must independently establish original-source role, lawful read scope,
opaque source identity, vendor selection, subject and period.** A source ID or
model-provided path is never file authority. The caller's metadata must not be
populated from a filename, source free text or a model's inference. Unknown
subject/date are passed as null; null is not a wildcard for a known host value.

`original_bytes` is **not proof of original authenticity**. This pure helper
cannot identify a derivative pretending to be an original just from its bytes.
A generated CSV with supported syntax will parse if a host incorrectly supplies
it as an original. Hash equality and parser reproduction do not solve that
provenance problem. The host's role/scope gate must run before migration.

This first API supports **original-byte-backed reports only**, not general
v1-to-v2 derivative migration. It has no derivative-role or original-ancestry
argument. Missing originals, streams, paths, mutable bytearrays and bytes
subclasses fail `ORIGINAL_LINEAGE_REQUIRED`. A derivative digest cannot replace
original positional lineage; changing the digest in old observations does not
make them match a newly parsed generated file. Do not pass derivative bytes as
`original_bytes` to work around this refusal. A future derivative entrypoint
requires independently verified original ancestry and exact positional lineage;
it must not relabel generated-file locators as original evidence.

## Explicit adapter gate

Only these exact IDs are supported, always at version `1.0.0`:

| Adapter ID | Unchanged normalizer selection and format gate |
|---|---|
| `pms-flat-yardi` | `yardi`, flat CSV/XLSX; BIFF explicitly refused |
| `pms-flat-realpage` | `realpage`, flat CSV/XLSX; BIFF explicitly refused |
| `pms-flat-entrata` | `entrata`, flat CSV/XLSX; BIFF explicitly refused |
| `onesite-detailed-realpage` | `realpage`, OLE/BIFF magic required, then existing detailed OneSite structural signature required |

The filename and issue-code naming do not choose the parser. Detailed OneSite
cannot silently run under the flat RealPage adapter, or vice versa. These are
reader identities, not universal vendor coverage or vendor authentication.
Optional spreadsheet dependencies and structural limits remain those of the
existing readers; migration installs nothing and has no format fallback.

## Preservation policy: `v1-preservation/1.0.0`

“Lossless” means **all v1-emitted observation semantics and evidence survive,
subject only to the explicit privacy omissions below**. It does not mean that
all content of a raw report was emitted by v1 or that raw identity is recoverable.

| v1 content | v2 representation |
|---|---|
| `version`, `pms_type`, digest | New contract version; exact vendor retained through adapter ID/version; verified digest on the original source |
| Scoped unit arrays | `units`, residential then commercial; each source collection's order preserved |
| Unit status/use | Exact values, including nulls; no interpretation of Model/Admin or missing use |
| Every evidence group and field citation | Ordered evidence maps, without merging or deduplication; only host `source_id` is added to each citation |
| `UNRESOLVED_UNIT_USE` issue observation | Moved exactly once to `unknown_use_units`, linked from its original issue; never promoted to scoped units |
| Two root scoped summaries | Both retained, including absent summaries, all statuses, reported counts and ordered citation groups |
| Flat summary's missing new fields | `row_derived_counts=null`, `vendor_status_counts={}`, `matched_fields=[]`, even when v1 says reconciled |
| `ONESITE_REPORT_SUMMARY` issue observation | One additional `scope="unknown"` summary; exact status, reported/row-derived counts, vendor counts and matched-field order; original citation map becomes one group, or none when empty; original issue links to it |
| All original issues | Exact order, multiplicity, code, severity and citation, including null; each gets its own position-derived ID |
| Scoped counts | Exact null/integer values, labelled enumeration only; coverage always unknown, no coverage citations |
| Root status | Blocked remains blocked; `normalized_unvalidated` becomes `observed_unvalidated` only if the unchanged v2 validator accepts it |

**Intentional omissions:** unit-record `unit_id` values (raw flat keys or OneSite
placeholders), and unit-record `tenant_name`/`phone`/`email` values equal to
`[REDACTED]`. Their field **citations** remain. No identity string is hashed to
make an observation ID, and no raw v1 object or raw-identity digest is embedded
in the output or ledger. The ledger counts omitted identity and placeholder
fields, rather than saving their values.

Source summaries are not substituted for enumeration. Complete nonnull scoped
counts describe retained observation enumeration, not complete property scope.
An all-null scoped count object has enumeration unknown; a partially nonnull
object has enumeration partial. Empty-scope zeros never prove no inventory.
Unknown-use units stay outside both scopes. OneSite's combined `admin_down`
count, even zero, never becomes a separate Down count. Unsupported observations
that v1 discarded cannot be recovered by migration.

### Identity and ordering

- Unit identity uses the existing `observation_id(evidence[0]["unit_id"])`:
  `unit_` plus full SHA256 of the source-bound canonical locator. The first
  physical occurrence anchors identity, not a raw unit/resident string.
- Summary/issue IDs are `sum_`/`iss_` plus the first 32 lowercase SHA256 hex
  characters of canonical JSON for
  `["v1-migration/1.0.0", prefix, source_id, source_sha256, pointer]`.
- `pointer` is the original v1 record position: `/summary/residential`,
  `/summary/commercial`, `/issues/<zero-based-index>`, or
  `/issues/<zero-based-index>/observation` for the OneSite summary.
  Absent summaries have a schema position even without a physical citation.
  Their IDs confer no missing evidence.
- Canonical JSON uses sorted object keys, preserved list order, compact
  separators, UTF-8, `ensure_ascii=False`, no newline and no nonfinite values,
  consistently with v2's Python canonical encoding. This is not a JCS claim.

Every issue retains its original index, including repeated identical issues.
OneSite linked units and the additional summary retain issue encounter order.
Changing original bytes or the host source ID changes identity; no cross-report
unit-master identity or semantic deduplication is claimed.

## Independent ledger verification

Before returning, migration validates v2 and performs an **inverse projection**
that reconstructs v1 semantics in memory, omitting only the declared identity and
placeholder fields. It compares exact canonical bytes with the independently
privacy-filtered actual parser output. This is not a second call to the forward
projection or merely a count comparison. It checks collection order and typed
links, all source fields, summary states, issue details and completeness policy.

`verify_preservation` repeats original-byte reproduction, validates an existing
v2 artifact against exact host scope, and runs that inverse comparison. It
returns only:

- policy version, original source SHA256, canonical v2 SHA256;
- equal SHA256s of privacy-filtered v1 and reconstructed v1 semantics;
- observation/scoped/unknown-use counts, summary and issue counts;
- evidence-group and citation-occurrence counts (multiplicity, not unique cells);
- counts of intentionally omitted identity and redacted-placeholder fields.

The equal semantic hashes do not contain or hash raw unit identifiers or
resident values. These source-derived artifacts still belong in private storage;
redaction is not egress permission. Store canonical v2 bytes separately and rerun
the verifier on read-back. File permissions, atomic writes, source role and
scope review, worker isolation and host authority are outside this pure module.

## Refusal and bounds

`MigrationError` has a static `.code` and static message. No input-bearing
exception is retained as `__cause__` or `__context__`. Do not log arguments or
traceback locals; raw original bytes are necessarily present in caller memory.

| Code | Meaning / next action |
|---|---|
| `INVALID_V1` | Invalid native types, values or host metadata; do not coerce |
| `INPUT_LIMIT_EXCEEDED` | Source/native/canonical budget exceeded; never truncate |
| `ORIGINAL_LINEAGE_REQUIRED` | Native immutable original bytes unavailable; no derivative shortcut |
| `SOURCE_HASH_MISMATCH` | Exact original digest differs from v1 claim |
| `UNSUPPORTED_ADAPTER` | Adapter ID is not an exact supported identity |
| `ADAPTER_SIGNATURE_MISMATCH` | BIFF/non-BIFF signature contradicts chosen adapter |
| `PARSER_REFUSED` | Unchanged normalizer could not parse the supplied bytes; inspect its separately sanitized diagnostic under host control |
| `PARSER_OUTPUT_MISMATCH` | v1 differs from the actual corresponding parser output |
| `UNREPRESENTABLE_V1` | Parser shape/payload or semantics cannot fit frozen v2; retain a reproducible private source/code pin and seek parent contract review |
| `PRESERVATION_MISMATCH` | Artifact fails independent preservation or exact host-scope validation |

Original bytes and native/canonical payloads are capped at 8 MiB. Native trees
are bounded before equality/copy/encoding: depth 16, 500,000 nodes including
keys, 50,000 entries per collection, 128 code points per string/key, 8 MiB
cumulative UTF-8 text. Only ordinary dict/list/str/int/null are allowed, integers
0..1,000,000. The unchanged readers and v2 validator impose their additional
bounds. A v1 result fitting its reader can still exceed expanded v2 capacity;
that must refuse rather than lose evidence. These bounds are not a hostile-file
sandbox or a replacement for host worker time/memory limits.

## Verification scope

Public tests consume actual `normalize_rent_roll` output on synthetic CSV/XLSX
and the existing embedded synthetic OLE/BIFF fixture. BIFF edge cases change the
fixture's bytes (SST strings, RK cells and LABELSST coordinates) and reparse with
xlrd, without fake-reader success outputs. They cover unknown use/status,
continuations, duplicate/conflicting units, orphan/future records, absent,
invalid and mismatched summaries, source identity, null citations, list ordering,
strict native refusals and schema-valid preservation tampering. Reader/projection
fault injection is separately labelled; no test depends on a private deal path.

A private original-byte preservation probe is distinct from source-scope review,
complete scoped four-count normalization, vendor-general compatibility, financial
execution or certification. Migration cannot upgrade any of those gates.
