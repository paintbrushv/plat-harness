# Source-bound ingestion reconciliation (Task 1.3)

**Candidate for parent interface and independent security review.** Separate
`ingest-resolution/1.0.0` overlay; frozen `ingest-observation/2.0.0`, v1 parsers,
migration and host authority remain unchanged. This is synthetic-tested source
verification, not real vendor/export acceptance, economic certification or engine
permission. No private original reports were read for this implementation.

The initial Task 1.3 candidate failed independent review on unexplained root
blocks and cross-field supplemental contradictions. This revision fixes those
defects; **Task 1.3 is not accepted pending parent adversarial re-review**.

## API and authority boundary

```python
from plat_harness.ingest.reconciliation import (
    reconcile_observations, reconcile_request, ReconciliationError,
)
from plat_harness.ingest.source_resolver import ByteSourceResolver, SEMANTICS
from plat_harness.adapters.review_bridge import host_registry

# These values come from independently authorized HOST intake, never request JSON.
resolver = ByteSourceResolver(
    originals=host_original_bytes,       # source_id -> exact built-in bytes
    sources=host_source_records,         # exact v2 records, including supplements
    expected_envelope_sha256=host_envelope_hash,
    subject_id=host_subject, as_of=host_as_of, adapter=host_adapter,
    intake_provenance=host_intake_provenance,
)
overlay = reconcile_observations(observations, decisions,
    host_registry=host_registry, source_resolver=resolver)

# Production request boundary: exact {observations, decisions}; native or UTF-8 JSON.
# Registry loader is bound internally to existing review_bridge.host_registry.
overlay = reconcile_request(request, source_resolver=resolver)
```

The internal API takes native dictionaries/lists, not encoded subdocuments.
`reconcile_request` accepts native JSON trees, exact `str` or exact `bytes`. It
rejects duplicate keys at every JSON level, extra keys, floats/nonfinite numbers,
subclasses, coercions, cycles and oversized input. There are no JSON path,
registry, rank, approval-boolean, resolver, supported-result or receipt arguments.
Artifact-reference lookup is deferred to Task 1.4; no path lookup exists here.
The embedding host must bind the resolver independently rather than construct it
from user arguments. Exact resolver type is required; arbitrary fake resolvers
returning `supported=True`, including subclasses, are not accepted.

The host-only registry argument must be callable. It is invoked anew on **every
reconciliation**, including empty-decision runs. Production uses the existing
pinned loader. Every supplied decision, including superseded history and repeated
decisions, passes the existing root `contracts._approval` over the entire payload
excluding `approval`. Registry membership, payload digest, human actor, timestamp
and full approval-provenance digest are not replaced by a new authority database.
Removal revokes; edited reason/locator/actor/time or payload refuses. This code
never creates an approval, changes ranks or enables an engine.

The resolver's exact expected envelope digest binds **all** validated canonical
v2 bytes, including untargeted observations, sources, citations, completeness,
issues and summaries. Subject, date, adapter and exact source records must match
independent host context. Null scope is not a wildcard: it yields `SCOPE_REQUIRED`
and cannot be filled from a decision or the CSV. Canonical hashing uses the
frozen v2 compact, sorted-key UTF-8 encoding (no newline), not claimed JCS.

### Original-intake requirement

For each original source, `intake_provenance` is exactly:

```text
source_id -> {sha256, role: "original", intake_id: "intake_" + 32 lowercase hex}
```

Every original record needs immutable host-supplied bytes with that exact SHA256.
Provenance entries and bytes must cover exactly the original IDs in the authorized
source records. Derivative records retain declared original ancestry; derivative
bytes are not evidence. Original IDs and source digests cannot be aliased.
The constructor snapshots bytes/records and provides only defensive context
copies. Supplemental records must have the same host subject/date.

**Valid CSV syntax, a SHA256, or an `intake_id` cannot authenticate original role.**
The host must independently establish who supplied each original and whether it
is an original rather than a generated normalized file. The provenance mapping is
an explicit host attestation requirement, not a new signature system. Do not
promote a generated seven-column export to original evidence to pass this dialect.
Host IDs must already be privacy-safe; this module is not an arbitrary PII scrubber.

## Exact decision schema: `ingest-mapping/1.0.0`

All fields are required. Every object rejects extras. Decisions are ordered;
changes have explicit unique `(observation_id, field)` membership, never wildcards.

| Field | Required value |
|---|---|
| `contract_version` | `ingest-mapping/1.0.0` |
| `decision_id` | `dec_` plus 32 lowercase hexadecimal digits |
| `envelope_sha256` | Exact host-pinned canonical v2 digest |
| `subject_id`, `as_of`, `adapter`, `sources` | Exact corresponding v2 values, including list order |
| `supplemental_sources` | List of exact authorized original v2 source records; unique IDs, no base-source redeclaration |
| `predecessor_sha256` | Null for first unique decision; otherwise digest of previous unique **whole decision including approval** |
| `supersedes` | Unique ordered list of earlier still-active whole-decision digests |
| `semantics` | Exact exported `SEMANTICS`: `{id: "bounded-csv-physical", version: "1.0.1", sha256: <spec digest>}` |
| `dimension` | `physical_unit` only |
| `changes` | Nonempty list of exact changes described below |
| `approval` | Existing exact host approval object, described below |

Each change is exactly:

```text
{observation_id, field, before, after, rule_id, citation, value_sha256}
```

- `observation_id` must be present exactly once in the original v2 collections.
- `field`: `unit_type` or `status` only.
- `before`: exact **original v2** enum or null, even for a successor decision.
  It is not an inferred intermediate value. Explicit supersession supplies the
  history binding independently of this immutable base value.
- `after`: nonnull `residential`/`commercial` for use, or
  `occupied`/`vacant`/`down` for status. No integers, prose or general expressions.
- `rule_id`: `use-token/1` or `status-token/1`, matching the field.
- `citation`: precise frozen v2 positional citation into a declared original in
  `sources` or `supplemental_sources`. Page citations are valid positional syntax
  but not supported by this verifier; they require evidence implementation.
- `value_sha256`: SHA256 of the exact source token's ASCII bytes, **nonnull** for
  decisions. It is checked against the actual cell, not accepted as a receipt.

Existing approval fields are exactly `approval_id`, `actor_id`, `actor_type`,
`approved_at`, `reason`, `record_locator`, `payload_sha256`. The existing host
registry has exact actor/payload/approval hashes. Decision approval reasons and
locators may contain sensitive reviewer text; **no raw approval strings** enter
the overlay. Only approval/decision hash references and opaque decision IDs do.
No executable mapping prose is accepted elsewhere in a decision.

### Decision construction example (host review still required)

```python
from copy import deepcopy
from hashlib import sha256
from plat_harness.ingest.contracts import canonical_bytes
from plat_harness.ingest.source_resolver import SEMANTICS

unit = observations['unknown_use_units'][0]
payload = {
    'contract_version': 'ingest-mapping/1.0.0',
    'decision_id': 'dec_' + '3' * 32,  # synthetic example only
    'envelope_sha256': sha256(canonical_bytes(observations,
        subject_id=host_subject, as_of=host_as_of)).hexdigest(),
    'subject_id': host_subject, 'as_of': host_as_of,
    'adapter': deepcopy(observations['adapter']),
    'sources': deepcopy(observations['sources']), 'supplemental_sources': [],
    'predecessor_sha256': None, 'supersedes': [],
    'semantics': deepcopy(SEMANTICS), 'dimension': 'physical_unit',
    'changes': [{
        'observation_id': unit['observation_id'], 'field': 'unit_type',
        'before': None, 'after': 'residential', 'rule_id': 'use-token/1',
        'citation': deepcopy(unit['evidence'][0]['unit_type']),
        'value_sha256': sha256(b'residential').hexdigest(),
    }],
}
# Submit payload through the EXISTING authenticated human review process.
# That process must bind this whole payload and full approval provenance.
decision = {**payload, 'approval': existing_host_approval_for_this_exact_payload}
```

The test helpers create **synthetic** registry records only, with canary reasons
and locators, to exercise the existing validator. They are not production review
or a facility for signing live decisions.

## Actually implemented source dialect

**`ascii-unquoted-csv/1`**, seven columns in exactly this order, first physical line:

```csv
Unit,Status,Unit Type,Property,As Of,Coverage,Status Definition
101,occupied,residential,synthetic_asset,2026-01-01,all_physical_units/1,occupied_vacant_down/1
```

- ASCII, comma delimiter, LF or CRLF, optional one terminal newline. No quoting,
  BOM, multiline fields, extra/missing columns, blank bands, repeated headers,
  totals, footnotes, charge/continuation rows, formulas or free-text columns.
  Unknown layouts are not reinterpreted as other formats.
- CSV sheet position is 1. Header is row 1; each unit is one physical row, with
  `row == row_end`. Unit identity is column 1, status column 2, use column 3,
  property column 4, as-of column 5, coverage column 6, definition column 7.
- Unit tokens must match `(?:[A-Z]{1,3}-?)?[0-9]{1,4}[A-Z]?`, globally unique in
  that source. No building-relative or composite identifiers are supported.
  Raw unit identity is used only in memory and never emitted.
- Property and date on every relied-on row must equal the independent host scope
  exactly. No names-to-host-ID mapping or date inference exists here.
- Use tokens: `residential`, `apartment` -> residential;
  `commercial`, `retail`, `office` -> commercial.
- Status tokens: `occupied`, `current`, `notice` -> occupied;
  `vacant` -> vacant; `down`, `offline`, `out of service` -> down.
  Matching is exact and case-sensitive, with no whitespace coercion.
- `occupied_vacant_down/1` means disjoint physical-unit categories: current/notice
  are occupied, vacant excludes down, down is out of service. A status cell is
  not verified without this independent governing definition on its source row.
  Admin, Model, NonRevenue, Admin/Down, zero, future and custom tokens are not Down.
- `all_physical_units/1` explicitly declares complete physical inventory for both
  residential and commercial interests, with no omitted units or ancillary
  spaces. It is independent of row enumeration. Coverage requires this token and
  the status definition on **every** original inventory row, exact one-to-one
  anchor coverage of every row, and a nonempty original inventory.
- Coverage currently supports one base original source (optionally underlying a
  derivative record). Multiple base originals require evidence work, even if
  only one has observations. Supplemental originals are not additional inventory
  rows and cannot create retroactive v2 ancestry.
- Supported adapter context: `pms-flat-yardi`, `pms-flat-realpage`,
  `pms-flat-entrata`, version `1.0.0`. This is compatibility with those existing
  synthetic flat-reader interfaces, **not** verified vendor branding/support.
  Unknown adapter/version returns `ADAPTER_UNSUPPORTED`.

The verifier checks original digests, precise column/physical positions, governing
header and status definition, property/date and actual raw unit identity. A
supplemental row must join to the target original row's unit identity and scope.
A recognized original token contradicting the proposed value yields
`SOURCE_CONTRADICTION`; a human cannot override source truth with a mapping.
Both recognized use **and** status on each relied-on row are checked, regardless
of which field the mapping proposes. The base row and all relied-on rows must
also agree pairwise after token-to-enum normalization. Comparing each supplement
only with an unknown base field is insufficient: two supplements can contradict
each other even though neither contradicts a recognized base value. Alias pairs
such as residential/apartment and occupied/current are equivalent, not conflicts.
Unsupported tokens require additional evidence, not a guessed classification.
Every nonnull base v2 use/status and every relied-on field citation is verified,
not just new mappings. Unknown base facts are not silently accepted as zero.

`resolver.verify(claim)` is host-internal. The exact claim is
`{target, field, value, citation, rule_id, value_sha256}`; `target` is the original
unit-ID anchor. A null fingerprint is reserved for host-generated base-fact
verification. Verified proof keys are exactly `state`, `claim_sha256`, `citation`,
`value_sha256`, `context_sha256`, `verifier`. The claim hash binds the target,
proposed enum, precise source citation and rule; the context hash binds expected
envelope, scope, adapter, authorized records and intake-provenance hash.
No proof or `supported` field can be supplied in decisions or requests.

`resolver.consistency(target, citations)` is a new host-only seam. `target` is the
precise original unit-ID anchor (column 1); `citations` is a native list of precise
use/status citations (columns 3/2). It uses the same strict digest, physical
locator, property/date, unit join, adapter and native-tree boundaries as `verify`.
The status definition is independently checked on the base and every joined row,
even for a use-only mapping. A verified consistency proof has exactly `state`,
`target_sha256`, `citations_sha256`, `context_sha256`, `verifier`; it contains no
raw tokens, unit IDs or reviewer text. It is **not** a field-value or coverage
proof and does not resolve any unknown field by itself.

Reconciliation supplies all citations for nonnull base fields and every active
mapping proposal for that observation, including conflicting proposals. It adds
the freshly computed consistency proof to that observation's `proofs`, or a
typed evidence request. `verify(claim)` also checks its individual joined row
against both base fields. Source contradictions cannot be removed by human
approval or a successful proof for a different field; any request keeps **all**
overlay counts null. Superseded decisions remain in history but are no longer
active reliance. Merely authorized or declared-but-unused supplemental rows are
not inspected by the consistency operation; no source discovery is added.

Bounded limitation: unknown base fields can be resolved by supported evidence,
but another relied-on row must have supported tokens for **both** counted fields.
An unsupported other-field token on that row returns `SOURCE_VALUE_UNSUPPORTED`,
not an inferred agreement. Partial supplemental-row semantics are not implemented.
The strengthened verification specification is `bounded-csv-physical/1.0.1` with
a new spec digest. Old `1.0.0` mapping approvals are not silently replayable;
host review must bind the new exact semantics. Enum meanings, token rules,
observation/decision/overlay schemas and the existing approval authority are
unchanged. This introduces no authority store.

`resolver.coverage(anchors)` computes coverage from the actual original table.
Its verified proof contains `state`, `anchors_sha256`, `citations` (coverage and
status-definition cells), `context_sha256`, `verifier`. Unsupported results have
exactly `{state: "evidence_required", code: <static code>}`.

BIFF, XLSX/ZIP, PDF, quoted/wrapped CSV and unsupported semantics remain evidence
requests. No dependency installation, workbook library, filesystem access,
network, model, GPU or engine call is used by this verifier.

## Overlay, completeness and retained history

Top-level keys are exactly:

```text
contract_version envelope_sha256 observations context_sha256 semantics state
coverage history head_sha256 resolutions evidence_requests dispositions
summary_dispositions counts
```

- `observations` is a defensive copy of the entire validated original envelope;
  its canonical bytes, issue/summary IDs, citations, ordering and nulls survive.
  Resolutions never modify v2 collections, ancestry or completeness.
- `history` entries contain `decision_id`, whole `decision_sha256`,
  `approval_sha256`, the complete safe `payload` excluding approval, and
  `state` (`active`/`superseded`). Supplemental original records live here only.
- Identical repeated whole decisions are idempotent and do not advance the head.
  Same decision ID with changed bytes refuses. Each new unique decision binds
  the preceding head. A superseded digest must be earlier, active and have
  **exactly the same field/observation membership** as its replacement. Partial,
  self, unknown, duplicate and twice-superseded replacements refuse.
- Active proposals with different values produce `DECISION_CONFLICT`, a null
  effective field and a blocker, not last-write-wins. An explicit successor can
  supersede all conflicting decisions while preserving their history.
- `resolutions` entries contain `observation_id`, nullable `unit_type`/`status`,
  `decision_sha256s` and source `proofs`. Unsupported active proposals cannot
  become observed facts. Proofs are always recomputed, not received from JSON.
- `evidence_requests` entries are exactly `code`, nullable `observation_id`,
  nullable `field`, nullable `reference_sha256`. References identify a decision,
  original issue or original summary without including raw reviewer text.
- `dispositions` preserve every original issue ID. Only the narrow linked
  `UNRESOLVED_UNIT_USE` issue is marked `resolved` after all its targets have
  source-supported effective use. All other issues remain `retained`; retained
  blockers add `ORIGINAL_BLOCKER`. Warnings do not independently block.
- A v2 root `status: blocked` with no explicit blocker issue adds
  `UNEXPLAINED_ROOT_BLOCKER`. Warnings, complete fields or a later mapping alone
  do not explain or clear it. This bounded overlay recognizes explicit blocker
  issues as the root's disposition path; only after **every** blocker is resolved
  and every other gate passes can an explained blocked envelope reconcile.
  The original root status remains `blocked` in the immutable observations.
  An unresolved summary or another retained blocker always continues to block.
- `summary_dispositions` retain **every** summary ID. No summary dialect is
  verified here: every non-absent summary, including one labelled reconciled in
  v2, adds `SUMMARY_VERIFICATION_REQUIRED`. Contradictions are never deleted or
  repaired. Combined Admin/Down summaries, even zero, cannot establish Down.
- `counts` is exactly residential/commercial four-count objects. If **any** gate
  remains unresolved, every overlay count stays null. If all gates succeed,
  deterministic code counts source-verified effective physical-unit enums.
  An empty commercial enumeration gets zero only with independently verified
  whole-inventory coverage, never because the list happened to be empty.

`state` is only `review_required` or `reconciled`. The latter means this bounded
intake's source facts, approved mappings, independent coverage and disjoint counts
are supported. It does **not** mean engine eligibility, economic certification,
execution permission, publish permission or real-source acceptance.

This validates the supplied chain only. There are no persistence writes or claims
of durable append-only history/latestness. **Task 1.4 must pin the expected latest
head and context**, reject rollback, persist original approval bytes separately
under private controls, and invoke reconciliation again on readback using the
current host registry and trusted originals. An old valid prefix alone is not
proof that no successor exists.

## Limits and safe failure

- Original bytes: 1 MiB per source, 8 MiB aggregate, at most 32 source records.
- CSV: at most 10,000 data rows plus header, exactly seven columns, at most
  128 characters per cell. Structural limits refuse rather than truncate.
- Decisions: at most 256 supplied entries and 256 changes per decision; at most
  32 supplements. Duplicate replays still consume the supplied-entry budget.
- Frozen native/JSON bounds also apply: 8 MiB cumulative text, depth 16,
  500,000 nodes including keys, collection length 50,000, strings 128 code points.
  Output is checked against these tree limits and an 8 MiB encoded overlay cap.
  Effective capacity is lower when citations/history/proofs consume the budget.
- Exceptions are static, raised outside handlers: `ReconciliationError` with
  `RECONCILIATION_REFUSED`, or `SourceResolutionError` with
  `INVALID_SOURCE_CONTEXT`. No cause/context retains inherited errors. Missing
  semantic evidence is a structured static request, not a source-bearing error.
- No logging occurs. Applications must not log request arguments, raw source
  bytes, host registry contents, or traceback locals. This is a bounded pure
  verifier, not a general hostile-document sandbox or substitute for intake
  provenance/privacy controls.

Common request codes include `SCOPE_REQUIRED`, `ADAPTER_UNSUPPORTED`,
`UNSUPPORTED_FORMAT`, `UNSUPPORTED_LAYOUT`, `SOURCE_LIMIT`,
`UNIT_IDENTITY_REQUIRED`, `AMBIGUOUS_UNIT_IDENTITY`,
`POSITION_MEANING_MISMATCH`, `SOURCE_SCOPE_MISMATCH`, `TARGET_UNIT_MISMATCH`,
`SEMANTIC_RULE_REQUIRED`, `SOURCE_VALUE_UNSUPPORTED`, `SOURCE_VALUE_MISMATCH`,
`SOURCE_CONTRADICTION`, `DOWN_DEFINITION_REQUIRED`, `COVERAGE_REQUIRED`,
`INVENTORY_MEMBERSHIP_MISMATCH`, `MULTISOURCE_COVERAGE_UNSUPPORTED`,
`BASE_FACT_UNVERIFIED`, `FIELD_EVIDENCE_REQUIRED`, `DECISION_CONFLICT`,
`ORIGINAL_BLOCKER`, `UNEXPLAINED_ROOT_BLOCKER`, `SUMMARY_VERIFICATION_REQUIRED`.

## TDD and verification evidence

### Initial implementation history (not current acceptance evidence)

All runs used the parent-provided private runner, isolated basetemps, offline/CPU
controls and disabled pytest plugin autoload. Evidence-directory basenames below
resolve beneath the parent-owned campaign root; each contains `command.json`,
`pytest.log`, `results.xml`, `result.json` and `test_ids.json`.

| Slice | RED evidence (assertion failures) | GREEN evidence |
|---|---|---|
| Real byte verifier | `t13_source_red.g9an9b29`: 12 failed | `t13_source_green.ieujtsv3`: 12 passed |
| Authority/overlay | `t13_overlay_red.5fjd_qi_`: 19 failed | `t13_overlay_green.0sqps0hz`: 31 passed |
| History/conflicts | `t13_history_assert_red.p6j6xzdz`: 3 failed, 26 passed | `t13_history_green.pajhtokm`: 41 passed |
| Coverage/completeness | `t13_coverage_red.s8nlqnk8`: 7 failed, 35 passed | `t13_coverage_green.70ah01ie`: 55 passed |
| Supplemental contradiction | `t13_contradiction_red.7338ps8x`: 1 failed | Included in coverage GREEN |
| Request boundary/multi-source | `t13_boundary_red.v95ggjrg`: 16 failed, 89 passed | `t13_boundary_green.bqtzlk7h`: 105 passed |
| Output bounds/native source keys | `t13_limits_red.99yl5sdf`: 2 failed, 105 passed | `t13_limits_green.xegnw_t2`: 107 passed |

Initial history run `t13_history_red.erv59i_g` had three unsupported-history
exception failures. Tests were tightened to assert the returned/refused outcome;
`t13_history_assert_red.p6j6xzdz` recorded the required assertion-level red **before**
implementation. All red runs exited 1; all listed green runs exited 0; none had
collection errors or skips.

Historical scoped implementation regression: `t13_full.k67dhe9r`, **1,133 passed**, no
failures/errors/skips, exit 0. Programmatic comparison against approved
`parent_t12_full.jlh6bnf8` preserves all **1,026** baseline test IDs and adds **107**.
That was the isolated model-free snapshot's scoped suite, not an excluded
synthetic-engine or live-model tier. The new real-source implementation is tested
with synthetic original bytes, including actual unchanged parser -> v1 migration
-> source-verifier -> overlay integration and a genuinely corroborated reconciled
mapping path. No unsupported report is counted as accepted.

At that initial handoff, all 101 preexisting files under harness/tests/docs were hash-
checked unchanged. `pyproject.toml` matches HEAD. No staging or commit was done;
HEAD remains `f983b834467925e7e7de6e747e9abdf007cccb2d`. Frozen contract,
migration, authority, review bridge, parsers and native/model/train/engine files
were not edited. The original repository was not written.

### Fail-open correction: current candidate evidence

Host-verified on the development machine (Linux aarch64). Before editing, the five
candidate files were privately snapshotted and hashed in `t13_pre_fix.22iopjs6`.
Its `candidate_sha256.json` matches the original handoff source/test pins; the
original documentation is preserved there. Initial red evidence was not replaced.

All runs below use the same parent runner and offline/CPU/disabled-plugin controls.
Evidence basenames resolve beneath
`your private evidence run root`.

| Slice | Assertion RED | GREEN |
|---|---|---|
| Original independent repro | `t13_fix_private_red.9jw4mniy`: 2 failed, 3 passed | `t13_fix_private_green.n16kejzx`: 5 passed |
| Root-block regressions and overlay suite | `t13_fix_root_assert_red.6chbtvo5`: 3 failed, 72 passed | `t13_fix_root_green.oxayo1hx`: 75 passed |
| Cross-field/pairwise consistency and both candidate suites | `t13_fix_cross_assert_red.ylqt74dj`: 20 failed, 121 passed | `t13_fix_cross_green.v3wwvxam`: 141 passed |

The root fix was green before implementing source consistency. Earlier fixture
development runs are retained: `t13_fix_root_red.ct5ij7gk` had one behavioral
assertion failure and two invalid warning fixtures; `t13_fix_cross_red.cg8ev3b_`
had eight fixture errors reported as test failures because the frozen legacy
parser drops unsupported-status rows. Fixtures were corrected without changing
production code, then the assertion-only red runs above were recorded before
their respective implementation changes. New seam-existence tests also fail by
assertion rather than import/attribute errors.

Permanent public synthetic coverage includes an unexplained root block with no
issues, warning-only and unlinked-field variants; legitimately resolved root
blocks and an additional retained blocker; both directions of cross-field source
contradiction; cross-supplement contradictions against a truly unknown original;
both proposal orders; consistent supplements and alias-equivalent positives;
unsupported other-field semantics; independent status definitions; exact locator
and digest boundaries; privacy-safe unchained errors; and unused-source isolation.

Current full scoped regression: `t13_fix_full.ncihcouf`, **1,167 passed**, no
failures/errors/skips, exit 0. The original independent repro also passes in its
separate run above. This is not an engine, live-model, GPU or real-source tier.
Parent independent adversarial re-review remains required before acceptance.

Programmatic test-ID comparison preserves all **1,133** previous scoped IDs and
adds **34** permanent synthetic cases. All **102** frozen files (101 preexisting
files under harness/tests/docs plus `pyproject.toml`) match the pre-fix manifest.
The manifest is `t13_pre_fix.22iopjs6/frozen_sha256.json`, SHA256
`d9bed81b09f78b2d93949a51459e683f435842bf25311464aa3895c8eb78256a`.
Only the five candidate files changed in the repository. No staging or commit;
HEAD remains `f983b834467925e7e7de6e747e9abdf007cccb2d`. Parent checkpoint/RESUME
files were not edited. No installs, network, models, GPU, engine execution or
private originals were used.

Current implementation/test SHA256 pins for parent re-review:

```text
harness/src/plat_harness/ingest/source_resolver.py
8e7cbb20007c7449c8ef5969cc265380114a2ea7569c91ff1db6463daf377b88
harness/src/plat_harness/ingest/reconciliation.py
384da6338870642b16c3eb40de15bc2347424a56184ed746350e9a87eb8f1e00
tests/test_ingest_reconciliation.py
d0958c871e66a628d16455d958888d0e53be2d1990a0639594ff6558b4881f41
tests/test_ingest_source_resolver.py
f362b6711143dca8452ad6ae58d391d37a553603894a8b4549d0ac750023a37b
```

Remaining product seams: actual original export dialects (including BIFF/PDF/XLSX),
source-summary semantics, richer identities, multi-original inventory coverage,
private immutable persistence/latest-head protection and downstream engine gates.
Do not edit sources to fit the dialect, silently adopt broader vendor aliases or
convert human assertions into source facts to close these seams.
