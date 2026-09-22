# Private ingestion acceptance snapshots

Task 2.1 supplies a host-only, Linux-local acceptance runner. It is a compatibility/refusal recorder, **not** Item 2 milestone acceptance, vendor certification, an approval service, or engine permission. Parent verification and independent review are required before adopting this implementation. Implementation fixtures are synthetic; no original deal-room content was read for this work.

## API and authority

```python
@dataclass(frozen=True)
class SourcePath:
    root: str
    relative_parts: tuple[str, ...]

@dataclass(frozen=True)
class AcceptanceLimits:
    max_manifest_bytes: int = 1024 * 1024
    max_entries: int = 128
    max_source_bytes: int = 8 * 1024 * 1024
    max_total_source_bytes: int = 256 * 1024 * 1024
    max_matrix_bytes: int = 8 * 1024 * 1024
    max_artifact_bytes: int = 128 * 1024 * 1024
    worker_wall_seconds: int = 60
    worker_cpu_seconds: int = 30
    worker_address_space_bytes: int = 1024 * 1024 * 1024
    run_wall_seconds: int = 600

runner = AcceptanceRunner(
    manifest_bytes=host_manifest_bytes,
    expected_manifest_sha256=independently_retained_manifest_pin,
    source_paths=host_source_handles,
    code_paths=reviewed_loaded_module_paths,
    expected_code_sha256s=independently_reviewed_code_pins,
    decision_chains=host_full_signed_chains,
    output_root=existing_private_output_root,
    limits=AcceptanceLimits(),
)
result = runner.run(host_new_run_id)  # exactly {pin, matrix}
historical = runner.read(host_run_id, expected=independently_retained_pin)
```

These arguments are independently authorized host state, never document text, model/tool request fields, or unreviewed user JSON. Syntactically valid source roles, intake IDs, opaque subject tokens and hashes do not authenticate original provenance or property scope. The embedding host must review those bindings. No CLI, request-JSON endpoint, dynamic adapter imports, arbitrary parser callbacks, registry argument, model path, permission rank, or approval flag is exposed.

`source_paths` keys must equal run-selected entry IDs. Excluded and declared-duplicate paths are not bound or opened. `decision_chains` keys must equal reconcile-requested entry IDs; empty chains are explicit lists. Chains are defensively copied, hash-bound as full ordered lists, never minted or persisted by the runner. Nonempty supplemental sources refuse reconciliation; they are not silently discarded.

### Code and runtime pins

The manifest binds the canonical hash of the full independent code-pin mapping. Paths must match the actual loaded module's `__file__` and `__spec__.origin`, not a copied file with an equal hash. File content, size and stable inode metadata are checked before source work and again at run checkpoints. Manifest values never select import targets or alter `sys.path`.

The base local closure is:

- `plat_harness` and its existing initializer dependencies `errors`, `glossary`, `models`, `ranks`;
- `plat_harness.ingest` and `ingest.acceptance`, `ingest.contracts`, `ingest.compat`, `ingest.pms_normalizer`.

Selecting detailed BIFF additionally binds `ingest.onesite`. Requesting reconciliation additionally binds `ingest.reconciliation`, `ingest.source_resolver`, root `contracts`, `adapters`, `adapters.review_bridge`, `adapters.slice_b`, `adapters.paths`, `millage`, `tools`, `tools.catalog`, `tools.certified_metric`, and `occupancy`. Package initializers are included. Existing lightweight model/tool type modules are transitive initializer imports; no inference, engine execution, training, financial calculation, or native model service is invoked.

Python, installed openpyxl and installed xlrd versions must match the manifest, including null when an optional dependency is absent. Spreadsheet libraries remain lazy parser imports. Version equality is **not** binary provenance for third-party packages, Python, libc or the operating system. Code pins are not a sandbox against malicious same-UID Python code modifying loaded objects.

## Manifest contract

`ingest-acceptance-manifest/1.0.0` requires exactly:

```
contract_version, corpus_id, corpus_revision, corpus_basis_sha256,
predecessor, change_kind, code_manifest_sha256, runtime, entries
```

Each entry requires exactly:

```
entry_id, source_id, source_sha256, source_size_bytes, source_role,
intake_id, subject_id, as_of, entity_kind, snapshot_id, corpus_use,
selection, exclusion_code, duplicate_of, adapter, expected_container,
requested_stage, decisions_sha256
```

The original UTF-8 manifest bytes are hashed before decoding. Duplicate JSON keys, floats, nonfinite numbers, excessive nesting, unknown fields and invalid tokens refuse. No token is normalized to repair an invalid identifier. JSON trees are bounded to depth 16, 500,000 nodes, 50,000 members per collection and 128 code points per string, as well as the byte ceilings. Counts, sizes, revisions and limits are strict integers, never booleans. Limits can only be lowered.

- Opaque IDs use the prefixes `corpus_`, `entry_`, `src_`, `intake_`, `snap_`, or `acc_` followed by 32 lowercase hex digits. SHA256 values have 64 lowercase hex digits. Subject/date and adapter ID/version use the unchanged observation-contract grammars.
- Roles: `original | derivative | unknown`. Executable intake requires original role and a nonnull host intake ID.
- Containers: `csv | xlsx | xls_biff | pdf | unknown`; stages: `observe | reconcile`.
- Entities: `property | portfolio | unknown`; uses: `inventory | comparison_only`.
- Selection: `run | excluded | declared_duplicate`. Run entries require null exclusion and duplicate fields.
- Exclusions: `OUTSIDE_FROZEN_SELECTION`, `DERIVATIVE`, `NON_RR_SOURCE`, `ALTERNATE_SNAPSHOT`, `PORTFOLIO_COMPARISON`, `DECLARED_DUPLICATE`. The last is reserved for declared duplicates.
- Duplicate entry/source IDs invalidate the manifest. A declared duplicate names a nonduplicate entry with matching source pins, snapshot, scope, role, container and use. It is not another observation.
- Repeated selected digests, identical path handles or opened inode aliases refuse all affected entries before any affected parser starts. No first-wins representative is selected.
- Byte-distinct selected snapshots for the same known subject/date are retained with `AMBIGUOUS_SNAPSHOT`. Different dates remain separate snapshots, not additional properties or a trend claim.
- Portfolio entries require null subject, comparison-only use and observe-only stage. They cannot supply property success or reconciled counts.
- Unknown property scope remains unknown and blocks acceptance. Source text cannot fill host scope.
- Observe requires null decision hash. Reconcile requires the full chain's canonical hash and property-inventory selection.

Canonical JSON uses sorted keys, compact separators, preserved list order, UTF-8, `ensure_ascii=False`, and no newline. This is not JCS. Source-path maps and original manifest bytes are not copied into artifacts.

## Original acquisition and worker boundary

Source roots must already be euid-owned 0700 directories. Ancestry is opened descriptor-by-descriptor from `/`, with no-follow directory handles, ownership/mode checks and attachment rechecks. Absolute paths cannot contain empty, dot or dot-dot components; relative parts are literal single components. No `resolve()`, filename search, glob or source scanning is performed. Output and source namespaces must not overlap.

Sources must be euid-owned, regular, nlink-one files with exactly 0400, 0600, 0444 or 0644 permissions. Reading uses mandatory `O_NOATIME`, no-follow and nonblocking flags. Unavailable no-atime access is refusal, not permission repair or a fallback. Before/after device, inode, size, mtime and ctime, name attachment, exact byte size and SHA256 are checked. Source copies are never persisted. No content, mode, mtime or access-time write to an original is intended.

The embedding process must be single-threaded, including native threads visible in `/proc/self/task`. A separate sequential Linux fork worker receives bytes and safe metadata, not source paths. The child closes unrelated inherited descriptors, redirects stdin/stdout/stderr to `/dev/null`, clears inherited environment authority/credentials, restores offline/no-CUDA controls, suppresses logs/warnings and applies CPU/address-space/core/file-size limits. It refuses if inherited mappings already exceed the address-space ceiling; setting RLIMIT_AS alone cannot remove inherited mappings.

IPC is explicit bounded JSON, not pickle or an unbounded queue. The worker emits a supervisor-owned parser-start marker. The parent validates strict observation scope/source/adapter bindings, preservation ledger structure and counts, and exact citation occurrence pointers/multiplicity. Adapter success flags, arbitrary receipts and unknown diagnostics are not accepted. Timeout, setup failure, abnormal exit, protocol overflow and cancellation never trigger parent parsing or another adapter. Workers are killed and reaped on failure/cancellation.

This supervises **trusted pinned parser code**; it is not a hostile-code or network sandbox. Offline environment flags and hidden CUDA do not enforce OS-level egress denial. The implementation contains no network/model/engine calls or dependency installation. Linux local filesystems and a trusted euid are prerequisites; malicious same-UID/root activity, privileged rollback, mutable mount topology and remote-filesystem behavior are outside the claimed isolation boundary.

### Finite work and measured scope

The initial unique-original budget is 256 MiB, capped at 8 MiB per source. Each relied-on original is acquired at most three times: initial acquisition, after staging before publication, and after durability/cleanup/end guards. Thus source replay is explicitly bounded, not an open-ended retry loop. Failed/excluded originals are not reparsed into success.

Each successful legacy pipeline invokes the unchanged normalizer three times: initial parse, migration reproduction and inverse-preservation reproduction. Repeating that pipeline at the two checkpoints gives at most nine normalizer invocations per relied-on source. `parser_started` counts unique entries, never those repeated calls. Worker wall/CPU/AS limits apply separately to each worker. The total run deadline also covers staging, verification, publication and final checks; an exhausted final budget refuses rather than returning a cached successful matrix. If insufficient time remains to publish safely, no completed matrix is returned.

A worker packet is capped at the smaller of the host artifact budget and 24 MiB. Observation and resolution artifacts retain their unchanged 8 MiB schema bounds. Actual serialized matrix and aggregate artifact bytes are checked before publication, not merely declared lengths. Large expansions may refuse below the original-byte cap.

Synthetic tested capacity includes a 256-unit CSV, an exact 8 MiB CSV with one inventory unit and bounded ignored preamble, real flat XLSX, and the existing embedded detailed BIFF fixture. The padded CSV is **not** proof of dense 8 MiB inventory capacity. Tests cover source cap+one refusal, a source above the frozen 1 MiB resolver cap retaining valid parse evidence with a reconciliation blocker, sparse XLSX refusal, chart-sheet ambiguity, CPU/wall/memory/setup/protocol/serialization refusal and process reaping. These are tested examples, not universal workbook/vendor throughput guarantees.

## Evidence and Amendment 1

The four registered adapter IDs are `pms-flat-yardi`, `pms-flat-realpage`, `pms-flat-entrata`, and `onesite-detailed-realpage`, version `1.0.0`. The selected unchanged parser is followed by original-byte migration and independent inverse preservation. Container magic is checked against explicit selection; it is not vendor identity. Failed input is never relabelled or routed to another reader. Native-v2 adapters, PDF extraction and arbitrary plugins are not registered.

Every positional citation occurrence is enumerated, including repeated evidence groups, summaries, issues and completeness. Ledger records contain exactly `pointer`, unchanged `citation`, `location`, and `value_check`. Location is `located | empty_or_implicit | missing | unsupported | null`; value_check is always `not_assessed`. Null issue citations remain null, not verified. CSV uses physical multiline record spans; XLSX uses worksheet relationships and actual XML cells rather than producer dimensions; detailed BIFF uses actual sheet/cell bounds. Unsupported chart-sheet ordering blocks. Blank positions cannot establish nonnull facts. BIFF formula-origin ambiguity remains explicit.

**Location is not value verification.** A real coordinate with the wrong semantic value is still only a location. Inverse preservation establishes faithful migration of the existing parser, not that the parser understood the original report correctly.

Optional reconciliation uses the unchanged production `reconcile_request`, exact `ByteSourceResolver`, and current pinned production registry loader. Observe-only runs do not read a registry. The resolver remains single-original, exact seven-column bounded ASCII CSV with its existing 1 MiB/10,000-row limits. Unsupported layouts, summary semantics, missing inventory, unsupported use/status and missing coverage remain blocked. Authority failures refuse rather than silently becoming observe-only success.

Under parent Amendment 1:

- Known-use CSV positives use the actual unchanged pipeline, both with an empty chain and with genuinely signed **same-value confirmation**. Confirmation is not unknown-use resolution.
- Full-chain supersession, revocation, edited approvals, missing authority and staged/final timing checks remain required.
- Actual unknown-use CSV rows are omitted by the frozen flat parsers with `UNSUPPORTED_UNIT_TYPE`. The runner preserves that blocked envelope and missing inventory; approvals cannot manufacture the omitted units.
- Detailed BIFF retains genuine unknown-use observations, but its container/adapter cannot reconcile through the frozen CSV resolver. Reconciled matrix counts remain null.
- Existing Item 1 isolated unknown-use resolver tests remain unchanged. They are isolated resolver coverage, **not** reachable end-to-end runner format acceptance.
- Genuine original-parser unknown-to-reconciled acceptance is deferred to a separately reviewed adapter/preservation/semantic-verifier extension. This runner does not close that gate or the two-archetype Item 2 milestone.

## Matrix, publication and historical reads

The result is exactly `{pin, matrix}`. Pin fields are `run_id`, `matrix_sha256`, `manifest_sha256`, and `code_manifest_sha256`. The matrix contract is `ingest-acceptance-matrix/1.0.0`, with manifest/corpus/code/runtime/limit identity, ordered entries, recomputed totals, artifact slots, and assessment. `input_identity_sha256` hashes canonical `{manifest, limits}`, removing only predecessor, change kind and corpus revision from that identity manifest.

Rows distinguish `excluded`, `declared_duplicate`, `refused`, `parsed_with_blockers`, `observed_unvalidated`, and `reconciled`. Every entry remains visible. Counts are null except for a genuinely reconciled row, where the unchanged overlay owns them. No unit counts are summed across properties, snapshots or portfolios. A distinct reconciled property requires all of its selected inventory snapshots to reconcile. Diagnostics are closed static namespaces/codes, not exception text or cell strings.

Assessment is `no_sources_ran` whenever no parser started, including empty/all-excluded/all-missing runs. Otherwise any selected refusal/blocker yields `blocked`; fully reconciled selected property inventory yields `reconciled_selected`; remaining clean observations yield `observed_unvalidated`. Every engine-eligibility and Item 2 milestone field is `not_evaluated`; engine-eligible total is always zero.

A fresh operation-owned 0700 staging directory contains only a 0600 `matrix.json` and allowlisted `<entry_id>.observations.json`, `.preservation.json`, `.citations.json`, or `.resolution.json` slots. No raw source/v1, approval reasons/locators, signed approval objects, source paths or debug logs are stored. Exact schemas, hashes, sizes, artifact-set equality and accounting are verified before Linux `renameat2(RENAME_NOREPLACE)` publication and again after fsync/readback. Same-ID retries, even identical ones, refuse. No mutable latest pointer or automatic permanent-file deletion exists.

Sources/code and preservation are checked after staging. Full signed chains are recomputed with fresh production registry loads immediately before publication and again after all durability, cleanup and end guards. The newest full chain, not an older empty prefix, is the final authority check. These sequential checkpoints are **not an atomic global registry/source/store transaction** and cannot prevent changes after return.

Failures at/after the publication boundary may be `WRITE_UNCERTAIN`; permanent files are retained. Host-independent exact pins are required to investigate historical bytes. `read()` validates immutable historical artifacts, not current sources, approvals or permission. It never turns a failed current-authority check into current authorization. A fresh current assessment needs a new run.

Successors require an exact independent predecessor run/matrix/manifest pin under the same output root, matching corpus identity, a new run ID and a validated primary change reason. Source corrections require new snapshot IDs; selection, adapter/code and review changes require greater corpus revisions. Exact repeat assessments may retain revision only with identical source/selection/code/review identity and limits. Historical bytes are never rewritten. This lineage is not a global latestness or branch oracle.

Top-level errors are closed static codes: `INVALID_HOST_INPUT`, `INVALID_MANIFEST`, `MANIFEST_HASH_MISMATCH`, `CODE_PIN_MISMATCH`, `RUNTIME_MISMATCH`, `UNSAFE_OUTPUT_ROOT`, `RUN_COLLISION`, `CANCELLED`, `ARTIFACT_REFUSED`, `WRITE_UNCERTAIN`. Public exception boundaries discard source-bearing exception causes/context. Private output is not approval for public export.
