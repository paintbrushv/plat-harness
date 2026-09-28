# PLAT V3 acceptance gates — 2026-09-28

This is the public, synthetic evidence ledger for mandate Sections 13, 26,
and 27. `BLOCKED` means a required behavior has no executable product path
yet. A related unit test is listed as partial evidence only when it exercises
the named boundary. No live source, credential, payment, or production writer
was used. The source-of-record remains unchanged.

## Exact reviewed public version set

| Component | Git commit | Role |
|---|---|---|
| `plat-harness` | `1108917fb1da55c373d32dc2330f00610db2e0a4` | merged temporal/opening runtime and self-commit installed verifier; main CI and integration passed |
| `plat-agent` | `a19d726ae8006e3ccbfc6984fcfef0d02abf02e3` | merged installed-package, MCP, explicit deal-data, and house backsolve adapters; main CI passed |
| `plat-costmodel` | `518142ecb8771e52fcc9985237fe1a6f97a76168` | reviewed cost package |
| `plat-multifamily-underwriting` | `10a88ed393e6d6611c8c710b5e15ef64e128af6b` | reviewed engine 0.1.1 and packaged MCP adapter; seven-job main CI passed |
| `plat-operations` | `171eb9622f53fe9d6b9703191d5a6832064100ed` | public Oak Ridge fixture |

An independent clean environment installed wheels built from these exact
merged SHAs, with `PYTHONPATH`, sibling path variables, host MCP command
overrides, and the deal-data-root override unset. All four wheels came from
`git archive` of the exact commits above.
`python -I`
loaded all four projects from site-packages, verified both producer content
pins and mismatch refusal, and reproduced TEST-001 year-two unlevered NOI
`1,039,354.8`, interior capex `1,358,150`, synthetic roof capex `400,000`,
total capex `1,758,150`, yield on cost
`0.06811800906400841517484098662`, and a withheld bid. The packaged
TEST-001 deal fixture loaded, direct underwriting/scenario/agency adapters
worked, the installed costmodel MCP server returned 11 tools, and the
installed `plat.underwriting.mcp/1` server returned validation, summary, and
feasibility over stdio with LTV `0.65`. The installed `engine.backsolve`
module produced three synthetic pricing artifacts from the installed agent
fixture while running from a temporary directory. `pip check` passed. The installed
harness temporal/opening modules imported from
site-packages and completed a one-row observed opening import with exact-cent
parity, no later-date carry-forward, and a refused unauthorized historical
read. Agent and underwriting main CI passed on the exact merged SHAs. The
previous installed integration set used agent `7ceb818` and underwriting
`0d106d6`; the new set removes the default underwriting MCP, house pricing
script, and lifecycle artifact-root source-checkout requirements. Federated
prompt dispatch still needs versioned prompt assets
and an actual host validation.

Observed wheel SHA-256 values from `git archive` of the tabled commits,
followed by `python -m pip wheel --no-deps --no-build-isolation`:

| Wheel | SHA-256 |
|---|---|
| `plat_agent-0.1.0-py3-none-any.whl` | `023bd882c78873ab766a7c4bbb163a3fe1d918e9b93a9a8bf76f969df963ba1d` |
| `plat_costmodel-0.1.0-py3-none-any.whl` | `8b72ec463da82bb41dbd38fb97e035d712e403bede0c3ed19b665bb648156fb0` |
| `plat_harness-0.1.0-py3-none-any.whl` | `6c6ca7deaa4d938c27ddd93eab93f8e8e087d749d354379d61500a18d312a018` |
| `plat_multifamily_underwriting-0.1.1-py3-none-any.whl` | `1157b4155b0628169e5319f08d2acf427f15b02229b745fad2988eb3af45ae60` |

These are observed build-file hashes, not a claim of byte-for-byte
reproducible wheels. The costmodel wheel bytes differed from the prior build
of the same Git commit; its installed source-content pin still matched.

The install check used a new Python 3.14 environment:

```text
python3 -m venv /tmp/plat-v3-final-install/venv
/tmp/plat-v3-final-install/venv/bin/python -m pip install --no-cache-dir \
    /tmp/plat-v3-final-install/wheels/plat_harness-0.1.0-py3-none-any.whl \
    /tmp/plat-v3-final-install/wheels/plat_agent-0.1.0-py3-none-any.whl \
    /tmp/plat-v3-latest-merged/wheels/plat_costmodel-0.1.0-py3-none-any.whl \
    /tmp/plat-v3-latest-merged/wheels/plat_multifamily_underwriting-0.1.1-py3-none-any.whl mcp
/tmp/plat-v3-final-install/venv/bin/python -m pip check
env -u PYTHONPATH -u PLAT_COSTMODEL_PATH -u PLAT_COSTMODEL_DEFERRED_PATH \
    -u PLAT_MULTIFAMILY_UNDERWRITING_PATH -u UNDERWRITING_ENGINE_PATH \
    -u PLAT_DEALS_ROOT -u UNDERWRITING_MCP_CMD -u PLAT_COSTMODEL_CMD \
    PLAT_HARNESS_EXPECTED_SHA=1108917fb1da55c373d32dc2330f00610db2e0a4 \
    /tmp/plat-v3-final-install/venv/bin/python -I scripts/verify_public_install.py
```

The committed script asserts site-packages origins, adapter version/content
pins and stale-content refusal, direct underwriting/scenario/agency imports,
TEST-001 economics and withheld bid, packaged deal fixture loading, and
bounded local stdio handshakes with both installed producer servers. It also
executes the installed house pricing module from a temporary directory,
checks the installed opening-state import, scoped historical read refusal,
and absence of forward-filled opening values.
The `V3 public integration` GitHub workflow installs the three exact producer
commits above and the checked-out harness commit that triggered the workflow.
It passes that commit as `PLAT_HARNESS_EXPECTED_SHA` to the verifier so a
later harness merge cannot silently retain an older self-pin. Its hosted
result is a separate gate from this VPS observation. The clean environment
completed the isolated script with local subprocess/socket access.

## Section 13: temporal and replay acceptance

| Case | Current evidence | Required proof still missing | Gate |
|---|---|---|---|
| 1. Late August expense | Immutable original-thesis object and intake revision store are tested separately. | Bitemporal NOI by effective August and knowledge cutoff September 10/20; frozen September 10 report must stay `100,000`, restated view `85,000`, repeat import unchanged. | BLOCKED |
| 2. Future lease | Date and unit evidence parsing exists. | Separate September preleasing from October physical occupancy under temporal query. | BLOCKED |
| 3. Backdated down unit | Occupancy normalizer rejects ambiguous status in synthetic tests. | Linked correction, as-issued count preservation, and overlapping vacancy/down reconciliation. | BLOCKED |
| 4. PM cutover | Intake source fingerprints and reconciliation gates are tested. | Scoped transaction/revision dedupe across two PM feeds, deposits, receivables, and opening-balance parity. Equal amounts must remain distinct. | BLOCKED |
| 5. Budget revision | Original acquisition thesis is frozen and later operations NOI is separate. | Independently query original budget, amendment, and latest forecast at old/new cutoffs. | BLOCKED |
| 6. Disorder/concurrency | `test_ingest_store.py` verifies a compare-and-swap winner and immutable append. | Domain event stream sequence gaps, duplicate delivery, and competing approval commands against an obsolete stream version. | BLOCKED |
| 7. Crash boundary | Intake store tests interruption at local file publication checkpoints. | Transactional event/outbox recovery and provider-outcome reconciliation before an uncertain external retry. | BLOCKED |
| 8. Rebuild | Reimported frozen thesis matches the original synthetic snapshot. | Zero-to-current and verified-checkpoint projection rebuild equality, pinned reducer/schema versions, zero action/task effects. | BLOCKED |
| 9. Historical access | Approved-execution tests reload the current host approval registry for execution and readback. | Historical records scoped by current workspace grants; revoked former access must fail after replay/restore. | BLOCKED |
| 10. Temporal integrity | Ingest date validation and missing-value refusals are tested. | Date-only precision, offset/DST boundaries, historical membership/joins, missing observations, and no future leakage. | BLOCKED |
| 11. Retention/version | Intake contract versions and redaction controls have synthetic tests. | Event schema upgrade and unsupported-version refusal, retained redactions through restore, and atomic cross-workspace-safe projection replacement. | BLOCKED |

The local [temporal assertion rehearsal](plat-v3-temporal-rehearsal.md) now
exercises portions of cases 1, 2, 3, 6, 9, 10, and 11 with eleven synthetic
tests. It also freezes an original thesis and reads a later operations actual
without replacing the issued figures. Its current-grant callback is synthetic,
and it has no migration, outbox, restore, or host query boundary. There is no
integrated product bitemporal store or host historical query API. The eleven
rows remain release acceptance requirements, not passing end-to-end cases.
The next work package must attach ingestion and authorization to the lane,
then execute the full scenarios above, including the missing PM cutover,
budget revision, crash boundary, rebuild, and retention/restore behavior.

## Sections 26–27: migration, economics, and authority

| Required evidence | Current executable evidence | Missing gate |
|---|---|---|
| Old/new reconciliation by property, period, account, unit category, source and revision | Synthetic intake acceptance matrix, exact four-row opening-state parity by stable property/period/account/unit category/source revision, and Oak Ridge NOI bridge: actual `339,150`, budget `353,200`, variance `-14,050`. | No cross-system migration cohort with scoped row-level parity and explained deltas. |
| Monetary golden expectations | Independent TEST-001 Decimal inputs/output and Oak Ridge arithmetic above. | Broader metrics with per-metric rounding/solver tolerances; realistic cohort volumes. |
| Fresh install and dependency combinations | Exact four-package merged-SHA clean install, missing/stale adapter refusal, `pip check`; MCP 1.30.0 initialized the installed costmodel server (11 tools) and packaged underwriting server (`plat.underwriting.mcp/1`, three tools) over stdio. The installed `engine.backsolve` produced three synthetic artifacts without a sibling checkout. | Published artifacts and forward/rollback compatibility matrix after new writes; versioned federated prompt dispatch and actual host proof. |
| Overlap, gaps, tombstones, amendments, concurrency, interrupted batches, repeat import, checkpoints | Intake store immutable revision/CAS and durability tests pass in a safe local artifact root. The synthetic opening-state lane detects sequence gaps, keeps a tombstone marker, serializes resume workers, and verifies an interrupted/repeated batch. | Restartable source-of-record migration with scoped watermarks, quarantines, amendments, recoverable source changes, and verified checkpoints against actual source evidence. |
| Schema upgrades | Intake contract version validation. | Event schema transformation and unsupported-version refusal in the historical ledger. |
| Pending actions and zero replay effects | Synthetic approved-execution gate and acceptance tests check no model, engine, network, or subprocess calls before authorization. | Durable action IDs/outcomes and outbox; zero external effects during shadow migration/replay; uncertain-outcome reconciliation. |
| Cross-workspace authorization and redaction | Protocol-level HTTP client returns `403` for unauthorized scope; approval registry is reloaded for synthetic execution/readback. | Actual host identity and tenant policy, historical access after revocation, backup/restore redaction, leakage checks. |
| Acquisition, operations, thesis-to-actual | TEST-001 acquisition and frozen thesis, Oak Ridge operations variance, and synthetic read connector pass separately. | One integrated temporal workflow with an original thesis, later actual, and current authorization across the same stable entity IDs. |
| Resource/correction measures | Agent full local suite on the new producer: `887 passed, 11 skipped` in `12.99s`; underwriting full local suite: `819 passed, 24 skipped` in `16.97s`. Its seven-job merged-main matrix passed after repairing prior CI failures. The prior 384 selected harness intake/auth/read tests passed in an isolated safe artifact root. | Realistic synthetic migration volume, replay time and peak resource use, freshness/watermark lag, correction burden and recovery-time observations. |

The [opening-state rehearsal](plat-v3-opening-state-rehearsal.md) is a pure
dry-run plan plus a bounded local synthetic importer. Six tests verify exact
four-row parity, independent grouped money expectations, restart after a
committed but uncheckpointed row, mapping/hash refusal, and current scoped
authorization. This is partial Section 26 component evidence, not a shadow
migration or a completed Section 27 suite. The missing gates in the table
remain release blockers.

The selected harness command is:

```text
python -m pytest -q tests/test_ingest_store.py tests/test_ingest_acceptance.py \
  tests/test_approved_execution.py tests/test_synthetic_read_http.py \
  tests/test_original_thesis.py
```

It requires a private, non-group-writable artifact ancestry for its file
integrity checks and local socket support for the HTTP client. An initial
run under a world-writable temporary parent failed by design; the rerun
under a safe artifact root passed. The temporary root was removed and the
original checkout mode restored. This is synthetic component evidence, not
a shadow migration.

## Staged release and cutover dossier

1. **Version freeze:** Record merged SHAs, package content pins, adapter
   versions, supported Python/platform matrix, source and target schemas,
   fixtures, and reproducible installation commands.
2. **Authority and input inventory:** Name the operator, environment, data
   cohort, current source writer, classified storage, identities, approvals,
   in-flight actions, credentials, retention rules, and stable-ID mappings.
3. **Isolated rehearsal:** Take and restore a verifiable input snapshot;
   perform bounded copy/backfill, catch-up, row-level reconciliation,
   projection rebuild, policy checks, interruption and recovery. Record
   observed duration/resource use and every quarantine/reject.
4. **Go/no-go:** Define owner-approved loss/recovery objectives, abort
   thresholds, freeze window, write fence, ownership/routing switch,
   health checks, and a forward-repair plan for writes after the switch.
5. **Observation and retirement:** Compare new and old paths during a
   defined observation window. Retire legacy consumers only after their
   dependencies and rollback roles are gone.

No production cohort, operator approval, cutover window, recovery objective,
or live host validation has been supplied. No tag, package publication, live
data migration, writer switch, or retirement is authorized by this evidence.
The current label remains `INTEGRATION_VERIFIED` for the public synthetic
path and `LIVE_CUTOVER_NOT_AUTHORIZED`; `RELEASE_CANDIDATE_READY` is blocked.
