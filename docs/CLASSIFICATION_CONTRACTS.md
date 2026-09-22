# Classification and policy contracts v1

`harness/src/plat_harness/contracts.py` implements pure, strict JSON validation.
It does **not** implement a learned classifier, financial engine, file resolver,
authorization service or automatic campaign migration. No engine, catalog, CLI or
model interface changes are required by this module.

## Classification

`validate_classification(payload, approval_registry=...)` requires exactly:

- `contract_version: classification/1.0.0`
- exact `subject_id`, `run_id`, lowercase `input_sha256`
- `deal_type`: `core`, `core+`, `lease-up`, `value-add`, `opportunistic`, or null
- unique `candidates` drawn only from that vocabulary
- finite numeric `confidence` in [0,1], excluding booleans (uncalibrated, not a
  financial confidence interval or evidence of model quality)
- `ambiguity`: explicit list of unresolved issues
- `conflicting_signals`: list of `{signal, evidence}`; never average conflicts
- `evidence`: list of locators, mandatory for a nonnull label
- `policy_pack: {policy_id, version}` with exact semantic version
- `state`: `review_required`, `abstained`, or `evidence_reviewed`
- `reason`, `review` (null unless evidence-reviewed), and
  `certification: uncertified_until_classifier_ships`

A null label requires abstention; abstention requires a null label. A selected
label must appear in candidates. Evidence-reviewed state requires a single
candidate, confidence at least 0.8, no ambiguity/conflict, exclusively reviewed
evidence, and a human review bound to the exact payload in a **host-owned**
approval registry. The 0.8 threshold is a conservative contract precondition,
not a calibrated classifier performance claim. Even evidence-reviewed outputs
remain `uncertified_until_classifier_ships` in this version. There is deliberately
**no certified state or certification transition API**.

Shipping this module never certifies an existing draft. Broker text and low-
confidence campaign inferences are not ground truth. A future certification
implementation needs separately approved business criteria, held-out results,
reviewed source evidence, exact input/run binding and explicit authorization.
The module does not edit existing campaign files.

### Evidence locators

Each locator is exactly `{artifact, sha256, subject_id, locator, source_kind}`.
`locator` is one of `{page: positive integer}`, `{sheet: nonempty string,
row: positive integer}`, or `{json_pointer: non-root RFC6901 pointer}`.
Source kinds are `broker_claim`, `analyst_inference`, `reviewed_evidence`.
Unknown fields, duplicate evidence and wrong-subject locators are rejected.
`artifact` is an opaque locator, **not authority to read a file**. Integration
must separately enforce approved roots, symlink/traversal defenses, actual
content hashes, source existence and semantic correspondence. A well-formed
locator alone proves none of those facts.

## Policy pack

`validate_policy_pack` requires `contract_version: policy-pack/1.0.0`,
`policy_id`, semantic `version`, exact `subject_id`, `status` (`draft` or
`approved`), `assumptions` and `approval`.

Assumptions are explicit records `{name,value,unit,evidence}`. Names and units
are allowlisted in `ASSUMPTION_UNITS`. Values are null or finite nonnegative
Decimal strings. Supplied values require locators. Duplicated assumptions and
unknown names/units fail. Missing values remain missing: an empty or approved
partial pack cannot bypass an engine's required-input gate. Domain ranges,
financing compatibility, periods, jurisdiction and economic adequacy remain
engine/business-policy checks; this validator is not an economic certifier.

An approved pack requires content-bound human approval. No classifier field can
supply financing, millage, exit cap, rent premium, target rent or investor hurdle:
unknown classification fields are rejected and no type-to-assumption defaults
exist. The historical roadmap's pack sketches are not approved policy packs.

`require_approved_policy` checks exact policy ID/version and subject, host-bound
policy approval, and reviewed classification evidence. Its returned policy is
still not engine execution permission or classifier certification.

## Human provenance and trust boundary

Approval is exactly `{approval_id, actor_id, actor_type: human, approved_at,
reason, record_locator, payload_sha256}` with timezone-aware timestamp.
Canonical hashing uses UTF-8 sorted compact JSON, `ensure_ascii=False`, no NaN.
The approval payload excludes only its own approval/review field. The host
registry maps approval ID to independently validated `actor_id`,
`payload_sha256` and `approval_sha256` (hash of the complete approval record,
including timestamp, reason and record locator). Changing provenance therefore
invalidates the registry match, even if the decision payload is unchanged.
Do not populate it from model output, broker documents or user-controlled tool
arguments. This module validates binding; the host must authenticate actors,
check revocation and load approvals from its trusted audit store. A human-looking
string by itself fails closed when no registry entry exists.

`record_human_override(original,replacement,approval,approval_registry=...)`
returns an append-only `classification-override/1.0.0` envelope with original
and replacement snapshots, both hashes and human provenance. The approval binds
`{contract_version, original_sha256, replacement_sha256}`. Subject, run and input
hash must match; the caller's original object is never modified. Neither the
override nor its human signature removes abstention/review, policy or financial
gates. Integrators must persist the envelope without overwriting source records.

## Verification

Run from the repository root:

```sh
.venv/bin/python -m pytest tests/test_contracts_next_stage.py -q
```

Tests include the five values, malformed/unknown fields, nonfinite confidence,
evidence forms, conflicts, abstention, untrusted approvals, tamper detection,
immutable overrides, exact policy versions and selected real deterministic
tool oracles. Model evaluation and financial certification remain distinct.

## Synthetic Slice B bridge v1

`adapters/review_bridge.py` connects these contracts to the existing
`adapters/slice_b.py` gates. **It adds no financial calculation or live
certificate.** Existing legacy requests, `underwrite`, `local-ask`, NullModel and
the ten-string `synthetic-underwrite --request ... --rank 2` request remain.
A classifier or policy document on its own still produces the historical typed
`CLASSIFIER_POLICY_INTEGRATION_NOT_IMPLEMENTED` refusal: a reviewed label is
not permission to execute.

### Host authority and execution approval

In addition to the existing engine root, private run root and exact
`PLAT_HARNESS_SLICE_B_APPROVAL_SHA256` pin, the operator must configure:

- `PLAT_HARNESS_CONTRACT_REGISTRY_PATH`: private absolute registry JSON path.
- `PLAT_HARNESS_CONTRACT_REGISTRY_SHA256`: lowercase SHA256 of those exact bytes.

The registry has **the same mapping** used by `contracts.py`: approval ID to
`{actor_id,payload_sha256,approval_sha256}`. There is no second approval database
or weaker approval flag. All paths, pins and rank belong to trusted host
configuration; no request or model field can provide them. The fixture registry
in tests is explicitly synthetic authority, not a claim that a person approved
real economics. Production actor authentication/revocation distribution remains
a host responsibility. Each readback rechecks current registry membership.

The existing request's `approval_path` can now point to exactly:

- `contract_version: slice-b-execution/1.0.0`
- `subject_id`, `run_id`, `input_sha256`, `policy_version` (the policy pack's exact
  semantic version; the classification additionally binds its policy ID)
- `synthetic: true`, `intake_validated: true`, exact canonical `analysis_window`
- `policy_sha256` and `evidence_sha256: {t12,broker,classification}` (byte hashes)
- `code_sha256`: exact map returned by `review_bridge.code_hashes()` for the
  existing engine, worker, wrapper, bridge and contracts library
- `tax_applicability: {asset_id,tax_year,parcel_ids,unit,millage_rate_mills}`
- `overrides: []`
- `approval`: the existing full human-provenance contract, independently
  registered against the entire envelope excluding only its `approval` member.

The wrapper validates this execution approval **as well as** the reviewed
classification and approved policy; it does not mint an execution approval by
translating either one. A compatibility view then passes through the old
nonfinancial gates, without changing canonical bytes or writing approvals.

Evidence references are constrained to persisted `inputs.json`, `broker.json`
and `t12.xlsx`, with exact content hashes. JSON pointers and workbook sheet/row
locators must resolve to present evidence. File paths, escapes, unbound sources
and unsupported page/PDF locators refuse. Existence does not establish source
truth: semantic interpretation still belongs to the approving reviewer.

### Tax and policy boundaries

Tax applicability must be present twice, identically: in the execution envelope
and `metadata.property_summary.tax_applicability` of the exact canonical input.
The asset must equal the subject, the year must be an integer equal to the
analysis-start calendar year, parcels must be explicit unique identifiers, and
`unit` must be `mills_per_1000`. Millage must equal the engine's existing
`property_tax_policy.millage_rate_mills` and the reviewed policy assumption.

Applicability is deliberately **not** inserted into `property_tax_policy`:
the existing engine requires that object's own exact schema. No engine/schema
change or adapter financial arithmetic is used. The starting-year binding is
not approval for a complete multiyear tax schedule.

The minimum policy bridge supports exact canonical comparisons for millage,
interest rate, exit cap and price basis. It does not apply these values: any
difference fails. Other supplied assumptions refuse `POLICY_NOT_IMPLEMENTED`;
nulls stay null and cannot satisfy required economics. Nonempty execution
`overrides` and tax `analyst_override` refuse `POLICY_CONFLICT`; implementing
reviewed economic overrides requires a separately tested contract, not a
boolean waiver. Classification's append-only override library remains unchanged;
a replacement classification still needs its own exact approved review.

### Post-execution review: immutable sidecar, never promotion

Run the existing engine/recon first. Its saved draft manifest and worker outcome
remain immutable, including original nulls, flags and blockers. A host can then
provide `PLAT_HARNESS_SYNTHETIC_REVIEW_PATH` and invoke the same CLI again, or
call `review_bridge.review_run(saved_identity)`. The API reloads the exact run;
it never trusts a supplied artifact directory or result values.

The review document is exactly:

- `contract_version: slice-b-recon-review/1.0.0`, safe unique `review_id`
- the four exact subject/run/input/policy identity fields, `synthetic: true`
- `scope`: unique comparison bucket IDs; includes at least gross potential rent,
  vacancy loss, concessions, loss to lease, insurance and real-estate taxes
- `artifact_sha256`: exact hashes for **all** `REVIEW_ARTIFACTS` (canonical,
  execution approval, classifier, policy, T12, broker, engine/validator/recon,
  worker result, actual child exit and original manifest)
- `code_sha256`, `decision: PASS|REFUSE`, nonempty `notes`
- `flag_dispositions`: exact `{sha256,reason}` for every actual recon flag; no
  omitted, duplicate or invented flag disposition
- `approval`: existing contracts approval format, independently host-registered.

Readback checks validation PASS, actual engine/recon execution, actual child exit
and timeout state, exact reviewed output bytes, full review provenance and
coverage. Exit 0 by itself cannot permit PASS. Unknown or duplicate schemas,
nonfinite/overflow values, unsafe paths, stale code, wrong asset/run/input/policy,
altered provenance and unregistered/revoked authority refuse.

Outcomes are intentionally distinct:

| Review outcome | Meaning |
|---|---|
| `REFUSED / RECON_EVIDENCE_INCOMPLETE` | At least one reviewed bucket has an absent row or null actual/broker/house cell. A host PASS cannot erase it. |
| `SYNTHETIC_SCOPED_PASS / RECON_COVERAGE_INCOMPLETE` | Every cell in the explicitly approved scope is present; other buckets remain uncovered and are listed. **Not a full reviewed PASS.** |
| `SYNTHETIC_REVIEWED_PASS / LIVE_CERTIFICATION_NOT_IMPLEMENTED` | Conditional full synthetic coverage eligibility: every bucket in the fixed 21-bucket recon surface has all three values, plus reviewed authority, flags and execution gates. No live certificate is issued. |

Only the last has `synthetic_pass_eligible=true`. Every outcome retains
`certification_eligible=false` and `certified=false`. The existing financial
metric API continues refusing. CLI always exits **2**, even if the engine child
exits 0 or the scoped review passes.

The real synthetic integration test reaches a **scoped** reviewed PASS and a
separate full-review refusal. The unchanged recon has unsupported/missing
surfaces (including actual replacement reserves); no nulls are fabricated or
replaced to manufacture a full PASS. Full-coverage eligibility is separately
unit-tested with a clearly labelled schema fixture, **not presented as real
engine/recon execution evidence**.

Sidecars are stored under
`<run-root>/<subject>/_reviews/<run-id>/<review-id>/{review.json,result.json}`.
Creation is exclusive/atomic. Exact byte readback and current authority are
required before return; identical reruns preserve bytes/mtimes and changed
reviews/results under the same ID refuse. Original engine files are never
promoted or edited. Review changes require a new review ID.

All source/review reads walk ancestors with no-follow descriptors and check
owned private single-link regular files. Numeric JSON tokens are bounded before
Decimal conversion; overflow and underflow that could become nonfinite values
or implicit zeros in the legacy recon are rejected. Unknown canonical economics
remain the existing engine's schema/validator responsibility. These protections
are not an OS sandbox against a malicious host owner.

### Regression execution

Use the existing UW interpreter and a **fresh private** pytest basetemp; normal
`/tmp` skips actual Slice B execution tests. Tests are in `test_review_bridge.py`,
`test_slice_b.py`, `test_slice_b_cli.py` and `test_contracts_next_stage.py`. Preserve
separate stdout/stderr, actual child exits and JUnit. New synthetic fixture
approvals are created only inside these private test directories. Never reuse a
prior run's output paths or reinterpret these fixtures as live approvals.
