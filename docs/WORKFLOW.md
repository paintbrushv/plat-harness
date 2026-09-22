# Reusable workflow state machine: `workflow/1.0.0`

**Task 6.1.** A resumable, content-addressed workflow state machine for
underwriting and ops runs. Workflow states are progress markers, never
financial certification: no stage implies underwriting correctness, and
`execution_authorized` only records a content-bound approval echo — actual
engine execution and live-deal authority stay with the separately reviewed
execution envelope. This module performs no financial math, launches no
threads, never invokes the engine, and stores only hashes and host paths,
never source bytes.

```python
from plat_harness import workflow

record = workflow.create_run(
    run_dir,                      # new empty directory (0700); never overwritten
    'synthetic_property',
    {'om':  {'sha256': ..., 'path': '/abs/om.pdf',  'format': 'pdf'},
     'rr':  {'sha256': ..., 'path': '/abs/rr.pdf',  'format': 'pdf'},
     't12': {'sha256': ..., 'path': '/abs/t12.xlsx', 'format': 'xlsx'},
     'debt': {'sha256': ..., 'path': '/abs/debt.csv', 'format': 'csv'}},
    {'millage': '25.31', 'horizon': '5'},   # missing values are null, never zero
    policy_sha256=..., code_sha256=...,    # optional exact pins
)
record = workflow.advance(run_dir, 'normalized')
record = workflow.advance(run_dir, 'review_required')
record = workflow.record_review(run_dir, {
    'decision_id': 'dec_001', 'status': 'approved', 'reviewer': 'host_reviewer',
    'resolutions': {'millage': {'value': '25.31', 'evidence': 'synthetic-policy'}}})
record = workflow.advance(run_dir, 'reconciled')
record = workflow.advance(run_dir, 'canonical_ready')
record = workflow.request_execution(run_dir, {
    'approval_id': 'appr_001',
    'content_sha256': record['content_identity'],
    'inputs_sha256': {n: i['sha256'] for n, i in record['inputs'].items()}})
record = workflow.advance(run_dir, 'engine_executed', artifact_sha256=...)
record = workflow.advance(run_dir, 'report_ready', artifact_sha256=...)
record = workflow.resume(run_dir)         # rebuilds state after interruption
```

## Stages, blockers, outcomes

Stages: `inspected → normalized → review_required | reconciled →
canonical_ready → execution_authorized → engine_executed → report_ready`.
`normalized → reconciled` is allowed directly when nothing
reconciliation-blocking is pending; otherwise the run must pass through
`review_required`. `execution_authorized` is reachable only through
`request_execution`, never `advance`.

Every returned record carries typed blockers that distinguish pending human
action (`pending_human_action`), unsupported engineering
(`unsupported_engineering`) and corrupt or vanished input (`corrupt_input`).
Nothing is all-or-nothing: present observations stay visible while blocked
fields remain in `blockers`, and a missing value is `null`, never zero.
`outcome` maps any record to one of the four documented outcomes —
`complete` (0), `needs_review_or_data` (2), `unsupported_input` (3),
`execution_error` (4) (`EXIT_CODES`).

## Content identity, run ids and resume

`content_identity(inputs, values)` is a SHA256 over the sorted input SHA256
pins, merged values and the policy/code pins; the `run_id`
(`run_[0-9a-f]{32}`) derives from it plus the subject. Identical content
gets an identical run id and byte-identical first event; changed inputs,
values or pins deterministically produce a different run id. Duplicate
creation of the same run at the same path refuses `DUPLICATE_RUN`; a dirty
or non-empty run directory refuses `OUTPUT_COLLISION` untouched.

The append-only journal (`events/NNNN.json`, `O_EXCL`, fsync) is truth;
`run.json` is a repairable snapshot that is verified against the journal on
every load, and rebuilt after a crash. `resume()` re-derives everything from
the journal, so resume is idempotent and needs no in-memory state. Resume
never reuses stale approvals and cannot quietly pick a different snapshot:
an override input must match the pinned SHA256 (`STALE` otherwise), and an
explicit re-pin of the same bytes at a new host path is journaled as
`inputs_repinned`. After approval, any source mutation refuses `STALE` —
restoring the pinned bytes unblocks the run. Mismatched policy/code pins
refuse `STALE` with the failing pin named.

## Review and execution authority

Human review decisions are recorded once (`DUPLICATE_DECISION` on reuse) and
can only resolve blocked default value fields (`millage`, `horizon`) —
never a missing source document (`INVALID_RESOLUTION`), never self-created
fields, and a decision never authorizes execution by itself. A cancelled
review leaves the run blocked at `review_required`.

`request_execution` refuses unless the stage is `canonical_ready`, no
unresolved blockers remain, an engine backend is configured, and the
approval pins the exact current `content_identity` and every input SHA256
(`STALE_APPROVAL` otherwise). Approval binding is checked again on every
journal replay. Engine completion pins an artifact SHA256
(`engine_executed`, `report_ready`); an engine failure is recorded as a
journaled `engine_error` event — visible, typed
(`execution_error`), never hidden — and the run stays at
`execution_authorized` for retry.

## Limits

Run directories are private (dirs `0700`, files `0600`), local-filesystem
only, and input pins are validated (absolute, non-traversing paths,
lowercase SHA256, ≤16 inputs). All errors are typed `HarnessError`s with
sanitized messages; source text, exception chains and captured output never
carry source content. No stage, outcome or exit code in this module is
financial certification or live-deal authorization; certification remains
unimplemented pending explicit human authority.